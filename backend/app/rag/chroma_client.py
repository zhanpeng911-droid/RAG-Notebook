"""Build the LangChain Chroma store in embedded or server mode.

A persistent embedded client is only safe with one process. The acceptance
and multi-worker deployment use a Chroma HTTP server so HNSW compaction is
owned by one process; embedded mode remains for single-process development.
"""
from __future__ import annotations

import os
import threading
from urllib.parse import urlparse

from langchain_chroma import Chroma

from app.utils.config import chroma_config
from app.utils.path_tool import get_abstract_path
from app.rag.vector_io import ObservedCollection, ObservedEmbeddings

_clients = {}
_client_lock = threading.Lock()


def _http_client(host, port, ssl):
    import chromadb
    from chromadb.config import Settings
    key = (os.getpid(), host, port, ssl)
    with _client_lock:
        # A forked child must neither reuse nor count inherited transports as
        # its own endpoints. Do not close a client's parent-process connection.
        for inherited in [entry for entry in _clients if entry[0] != key[0]]:
            del _clients[inherited]
        if key not in _clients:
            # Reuse one transport for note and document collections. In
            # particular, self-heal must not leak another Chroma System/pool.
            if len(_clients) >= 4:
                raise RuntimeError("Too many Chroma endpoints in one process")
            _clients[key] = chromadb.HttpClient(
                host=host, port=port, ssl=ssl,
                settings=Settings(chroma_http_max_connections=16,
                                  chroma_http_max_keepalive_connections=8))
        return _clients[key]


def chroma_server_url() -> str:
    return (os.getenv("CHROMA_SERVER_URL") or "").strip()


def build_chroma(collection_name: str, embedding_function):
    from app.config.validator import get_settings
    settings = get_settings()
    embedding_endpoint = (settings.OLLAMA_BASE_URL if settings.EMBED_MODEL_TYPE.upper() == "OLLAMA"
                          else "https://dashscope.aliyuncs.com")
    if not isinstance(embedding_function, ObservedEmbeddings):
        embedding_function = ObservedEmbeddings(embedding_function, embedding_endpoint)
    url = chroma_server_url()
    if url:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        client = _http_client(parsed.hostname or "localhost", parsed.port or 8000,
                              parsed.scheme == "https")
        store = Chroma(client=client, collection_name=collection_name,
                       embedding_function=embedding_function)
    else:
        store = Chroma(
            collection_name=collection_name, embedding_function=embedding_function,
            persist_directory=get_abstract_path(chroma_config["persist_directory"]),
        )
    # The installed langchain_chroma adapter exposes its native collection here.
    # Fail explicitly if an SDK upgrade changes that boundary.
    collection = getattr(store, "_chroma_collection", None)
    if collection is None:
        raise RuntimeError("Unsupported LangChain Chroma collection adapter")
    store._chroma_collection = ObservedCollection(collection, url or "embedded.local")
    return store
