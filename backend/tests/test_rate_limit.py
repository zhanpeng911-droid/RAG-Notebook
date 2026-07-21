from types import SimpleNamespace

from app.core.rate_limit import _build_rate_limit_key


def test_build_rate_limit_key_uses_route_and_token_hash():
    request = SimpleNamespace(
        url=SimpleNamespace(path="/chat/agent/query/stream"),
        headers={"Authorization": "Bearer test-token"},
        client=SimpleNamespace(host="127.0.0.1"),
    )

    key = _build_rate_limit_key(request)

    assert key.startswith("rate_limit:chat:agent:query:stream:token:")
    assert "test-token" not in key


def test_build_rate_limit_key_falls_back_to_forwarded_ip():
    request = SimpleNamespace(
        url=SimpleNamespace(path="/note/create"),
        headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
        client=SimpleNamespace(host="127.0.0.1"),
    )

    key = _build_rate_limit_key(request)

    assert key == "rate_limit:note:create:ip:203.0.113.10"


def test_build_rate_limit_key_falls_back_to_client_ip_when_no_proxy_header():
    request = SimpleNamespace(
        url=SimpleNamespace(path="/review/today"),
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )

    key = _build_rate_limit_key(request)

    assert key == "rate_limit:review:today:ip:127.0.0.1"
