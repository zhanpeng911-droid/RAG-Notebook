"""Bounded embedding calls and payload-free dependency failure diagnostics."""

from __future__ import annotations

import asyncio
import time
import traceback
from urllib.parse import urlsplit

EMBED_BATCH_SIZE = 256
CONNECT_ATTEMPTS = 2
COLLECTION_WRITES = frozenset({"add", "upsert", "update", "delete"})


def safe_endpoint(url: str) -> str:
    parsed = urlsplit(url if "://" in url else "https://" + url)
    host = parsed.hostname or "unconfigured"
    return f"{host}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"


def exception_details(error: BaseException) -> dict:
    """Keep exception type/errno/frames; never serialize args, bodies or headers."""
    chain: list[dict[str, object]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "errno": getattr(current, "errno", None),
                "winerror": getattr(current, "winerror", None),
            }
        )
        current = current.__cause__ or current.__context__
    return {
        "exception_chain": chain,
        "frames": [
            {
                "file": frame.filename.replace("\\", "/").rsplit("/", 1)[-1],
                "line": frame.lineno,
                "function": frame.name,
            }
            for frame in traceback.extract_tb(error.__traceback__)[-12:]
        ],
    }


class VectorDependencyError(RuntimeError):
    def __init__(
        self,
        operation: str,
        endpoint: str,
        cause: Exception,
        *,
        write_uncertain: bool,
        elapsed_s: float,
    ):
        self.operation = operation
        self.endpoint = endpoint = safe_endpoint(endpoint)
        self.write_uncertain = write_uncertain
        self.details = {
            "operation": operation,
            "endpoint": endpoint,
            "write_uncertain": write_uncertain,
            "elapsed_s": round(elapsed_s, 3),
            **exception_details(cause),
        }
        super().__init__(f"{operation} failed at {endpoint} ({type(cause).__name__})")


class VectorBatchError(RuntimeError):
    def __init__(self, cause: Exception, *, batch: int, total: int, confirmed: int):
        self.writer_stopped = (
            isinstance(cause, VectorDependencyError)
            and not cause.write_uncertain
            and (
                cause.operation.startswith("embedding.")
                or cause.operation in {"chroma." + name for name in COLLECTION_WRITES}
            )
        )
        self.details = {
            "batch": batch,
            "total_batches": total,
            "confirmed_batches": confirmed,
            "writer_stopped": self.writer_stopped,
            **(
                cause.details
                if isinstance(cause, VectorDependencyError)
                else {"operation": "vector.add_documents", **exception_details(cause)}
            ),
        }
        super().__init__(
            f"vector batch {batch}/{total} failed; "
            f"operation={self.details['operation']}; "
            f"confirmed_batches={confirmed}; writer_stopped={self.writer_stopped}"
        )


def _connect_failure(error: Exception) -> bool:
    # Read/write timeouts are deliberately not retried: a vector mutation may
    # already be running, and an embedding response may already be billable.
    import httpx

    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def embedding_http_options(connect_seconds=5.0, read_seconds=60.0):
    import httpx

    return {
        "timeout": httpx.Timeout(read_seconds, connect=connect_seconds, pool=connect_seconds),
        "limits": httpx.Limits(max_connections=16, max_keepalive_connections=8),
    }


class ObservedEmbeddings:
    """Bound peak request allocations, preserving all texts and their order."""

    def __init__(self, inner, endpoint: str):
        self.inner = inner
        self.endpoint = safe_endpoint(endpoint)

    def _call(self, operation, call):
        started = time.perf_counter()
        for attempt in range(CONNECT_ATTEMPTS):
            try:
                return call()
            except Exception as exc:
                if attempt + 1 < CONNECT_ATTEMPTS and _connect_failure(exc):
                    time.sleep(0.1)
                    continue
                # Embedding computes values only. This failed call cannot
                # issue a later Chroma upsert after this synchronous return.
                raise VectorDependencyError(
                    operation,
                    self.endpoint,
                    exc,
                    write_uncertain=False,
                    elapsed_s=time.perf_counter() - started,
                ) from exc

    def embed_documents(self, texts):
        vectors = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            result = self._call("embedding.documents", lambda: self.inner.embed_documents(batch))
            if len(result) != len(batch):
                raise VectorDependencyError(
                    "embedding.documents",
                    self.endpoint,
                    ValueError("embedding count mismatch"),
                    write_uncertain=False,
                    elapsed_s=0,
                )
            vectors.extend(result)
        return vectors

    def embed_query(self, text):
        return self._call("embedding.query", lambda: self.inner.embed_query(text))

    async def aembed_documents(self, texts):
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text):
        return await asyncio.to_thread(self.embed_query, text)


class ObservedCollection:
    """Central adapter for the installed LangChain Chroma native collection."""

    def __init__(self, inner, endpoint: str):
        self._inner = inner
        self._endpoint = safe_endpoint(endpoint)

    def __getattr__(self, name):
        method = getattr(self._inner, name)
        if name not in {"add", "upsert", "update", "delete", "get", "query", "count"}:
            return method

        def call(*args, **kwargs):
            started = time.perf_counter()
            try:
                return method(*args, **kwargs)
            except Exception as exc:
                # A typed connect/pool failure happened before request delivery.
                # Read/write timeouts provide no remote stop proof. Neither
                # case is automatically retried for a vector mutation.
                raise VectorDependencyError(
                    "chroma." + name,
                    self._endpoint,
                    exc,
                    write_uncertain=name in COLLECTION_WRITES and not _connect_failure(exc),
                    elapsed_s=time.perf_counter() - started,
                ) from exc

        return call
