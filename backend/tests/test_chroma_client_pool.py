"""Client reuse checks with injected SDKs: no sockets or Chroma server."""

import sys
from types import SimpleNamespace

from app.rag import chroma_client


def test_http_client_reuses_transport_across_collections(monkeypatch):
    created = []

    def build(**kwargs):
        client = SimpleNamespace(**kwargs)
        created.append(client)
        return client

    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(HttpClient=build))
    monkeypatch.setitem(sys.modules, "chromadb.config", SimpleNamespace(Settings=lambda **kw: kw))
    monkeypatch.setattr(chroma_client, "_clients", {})
    monkeypatch.setattr(chroma_client.os, "getpid", lambda: 12345)
    first = chroma_client._http_client("localhost", 8001, False)
    assert chroma_client._http_client("localhost", 8001, False) is first
    assert len(created) == 1
    assert first.settings == {
        "chroma_http_max_connections": 16,
        "chroma_http_max_keepalive_connections": 8,
    }
    assert chroma_client._http_client("localhost", 8002, False) is not first


def test_http_client_never_reuses_another_process_transport(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "chromadb",
        SimpleNamespace(HttpClient=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    monkeypatch.setitem(sys.modules, "chromadb.config", SimpleNamespace(Settings=lambda **kw: kw))
    monkeypatch.setattr(chroma_client, "_clients", {})
    pid = [12345]
    monkeypatch.setattr(chroma_client.os, "getpid", lambda: pid[0])
    parent = chroma_client._http_client("localhost", 8001, False)
    for port in (8002, 8003, 8004):
        chroma_client._http_client("localhost", port, False)
    pid[0] = 12346
    assert chroma_client._http_client("localhost", 8001, False) is not parent
    assert len(chroma_client._clients) == 1
