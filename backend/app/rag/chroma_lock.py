"""Cross-process serialization and batching for the persistent Chroma store.

Chroma's persistent client uses SQLite with a delete journal. Multiple uvicorn
workers and the Celery worker writing the same directory concurrently can make
SQLite fail (or abort a worker), and large documents exceed the collection's
maximum batch size. Writes therefore take one inter-process file lock and are
split below the collection limit; reads stay lock-free because note/read
endpoints do not touch Chroma.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Sequence

from filelock import FileLock

from app.utils.config import chroma_config
from app.utils.path_tool import get_abstract_path
from app.rag.vector_io import VectorBatchError

MAX_BATCH_SIZE = 512
_LOCK_TIMEOUT_SECONDS = 600
_guard = threading.Lock()
_lock: FileLock | None = None


def _lock_path() -> str:
    persist_dir = get_abstract_path(chroma_config["persist_directory"])
    os.makedirs(persist_dir, exist_ok=True)
    return os.path.join(persist_dir, ".chroma-write.lock")


def server_mode() -> bool:
    return bool((os.getenv("CHROMA_SERVER_URL") or "").strip())


@contextmanager
def write_lock():
    """Exclusive lock shared by API workers, Celery, and scripts.

    A Chroma HTTP server serializes its own mutations; the file lock would
    only add cross-process queueing, so it is skipped in server mode.
    """
    if server_mode():
        yield
        return
    global _lock
    with _guard:
        if _lock is None:
            _lock = FileLock(_lock_path(), timeout=_LOCK_TIMEOUT_SECONDS)
        lock = _lock
    with lock:
        yield


def locked(func, /, *args, **kwargs) -> Any:
    with write_lock():
        return func(*args, **kwargs)


LOCKED_METHODS = frozenset({
    "add", "upsert", "update", "delete",
    "query", "get", "count", "modify", "fork",
})


class LockedCollection:
    """Serialize every native Chroma operation across API/worker processes.

    Chroma's persistent client keeps an in-process HNSW segment cache. Two
    processes compacting/reading the same collection produced
    "Error loading hnsw index" and killed API workers during concurrent large
    uploads. Embedding still happens before ``upsert``; the lock covers only
    native collection calls.
    """

    def __init__(self, collection):
        self._inner = collection

    def __getattr__(self, name):
        value = getattr(self._inner, name)
        if name not in LOCKED_METHODS or not callable(value):
            return value

        def guarded(*args, **kwargs):
            with write_lock():
                return value(*args, **kwargs)

        return guarded


def lock_collection_access(store) -> None:
    """Wrap a LangChain Chroma store's native collection with the lock."""
    if server_mode():
        return
    collection = getattr(store, "_chroma_collection", None)
    if collection is not None and not isinstance(collection, LockedCollection):
        store._chroma_collection = LockedCollection(collection)


def add_documents_batched(store, documents: Sequence, *,
                          ids: Sequence[str] | None = None,
                          batch_size: int = MAX_BATCH_SIZE) -> None:
    """Add every document under one lock, never above Chroma's batch limit."""
    if not documents:
        return
    if ids is not None and len(ids) != len(documents):
        raise ValueError("documents and ids must have the same length")
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("invalid batch size")
    for start in range(0, len(documents), batch_size):
        end = start + batch_size
        try:
            if ids is None:
                store.add_documents(list(documents[start:end]))
            else:
                store.add_documents(
                    list(documents[start:end]), ids=list(ids[start:end]))
        except Exception as exc:
            raise VectorBatchError(
                exc, batch=start // batch_size + 1,
                total=(len(documents) + batch_size - 1) // batch_size,
                confirmed=start // batch_size) from exc
