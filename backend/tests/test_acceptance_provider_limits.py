"""Provider concurrency cleanup without binding or opening a socket."""

from types import SimpleNamespace

import pytest

from tools.backend_acceptance.deterministic_provider import (
    BoundedProviderServer,
    ThreadingHTTPServer,
)


def _server():
    server = object.__new__(BoundedProviderServer)
    calls = []
    server._slots = SimpleNamespace(
        acquire=lambda: calls.append("acquire"), release=lambda: calls.append("release")
    )
    return server, calls


def test_provider_releases_slot_if_thread_creation_fails(monkeypatch):
    def fail(*args):
        raise RuntimeError("could not create worker")

    server, calls = _server()
    monkeypatch.setattr(ThreadingHTTPServer, "process_request", fail)
    with pytest.raises(RuntimeError):
        server.process_request(None, None)
    assert calls == ["acquire", "release"]


@pytest.mark.parametrize("fail", [False, True])
def test_provider_releases_slot_after_handler_returns_or_fails(monkeypatch, fail):
    def handle(*args):
        if fail:
            raise RuntimeError("handler failed")

    server, calls = _server()
    monkeypatch.setattr(ThreadingHTTPServer, "process_request_thread", handle)
    if fail:
        with pytest.raises(RuntimeError):
            server.process_request_thread(None, None)
    else:
        server.process_request_thread(None, None)
    assert calls == ["release"]
    assert server.request_queue_size == 128
