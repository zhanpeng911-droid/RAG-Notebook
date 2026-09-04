"""rate_limit 测试 —— 分桶键：已验证 user_id / 可信代理 IP / 路由模板。"""
from types import SimpleNamespace

import pytest

from app.core import rate_limit as rl
from app.core.rate_limit import _build_rate_limit_key, _trusted_client_ip


def _req(path, headers=None, client_host="127.0.0.1", route_path=None):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers=headers or {},
        client=SimpleNamespace(host=client_host),
        scope={"route": SimpleNamespace(path=route_path) if route_path else None},
    )


def test_key_uses_verified_user_id_not_token():
    req = _req("/chat/agent/query/stream",
               headers={"Authorization": "Bearer some-token"},
               route_path="/api/v1/chat/agent/query/stream")
    key = _build_rate_limit_key(req, user_id="user-abc")
    assert key == "rate_limit:api:v1:chat:agent:query:stream:user:user-abc"
    assert "some-token" not in key


def test_key_uses_route_template_not_dynamic_path():
    # 两条不同资源路径，但同一路由模板 → 同一桶（换 note ID 不重置额度）
    req1 = _req("/api/v1/note/111", route_path="/api/v1/note/{note_id}")
    req2 = _req("/api/v1/note/222", route_path="/api/v1/note/{note_id}")
    k1 = _build_rate_limit_key(req1, user_id="u1")
    k2 = _build_rate_limit_key(req2, user_id="u1")
    assert k1 == k2
    assert "{note_id}" in k1


def test_key_anonymous_uses_client_ip_without_trusted_proxy(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    req = _req("/note/create",
               headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
               route_path="/api/v1/note/create")
    key = _build_rate_limit_key(req, user_id=None)
    # 未配置可信代理：不信任 X-Forwarded-For，用直接对端
    assert key == "rate_limit:api:v1:note:create:ip:127.0.0.1"


def test_key_anonymous_trusts_forwarded_when_peer_trusted(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1,10.0.0.5")
    req = _req("/note/create",
               headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
               route_path="/api/v1/note/create")
    key = _build_rate_limit_key(req, user_id=None)
    assert key == "rate_limit:api:v1:note:create:ip:203.0.113.10"
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)


def test_trusted_client_ip_ignores_forwarded_when_peer_not_trusted(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.5")
    req = _req("/x", headers={"X-Forwarded-For": "1.2.3.4"}, client_host="127.0.0.1")
    assert _trusted_client_ip(req) == "127.0.0.1"
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)


# ---------- fail_closed：限流存储不可用时的故障策略 ----------

@pytest.mark.asyncio
async def test_fail_closed_returns_503_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(rl, "_is_rate_limit_enabled", lambda: True)

    async def _none():
        return None
    monkeypatch.setattr(rl, "connect_redis", _none)

    req = _req("/api/v1/chat/agent/query/stream", route_path="/api/v1/chat/agent/query/stream")
    dep = rl.rate_limit(limit=1, window=60, fail_closed=True)(req)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await dep
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_cheap_read_degrades_open_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(rl, "_is_rate_limit_enabled", lambda: True)

    async def _none():
        return None
    monkeypatch.setattr(rl, "connect_redis", _none)

    req = _req("/api/v1/note/list", route_path="/api/v1/note/list")
    dep = rl.rate_limit(limit=1, window=60)(req)
    assert await dep is None  # 便宜读取降级放行


@pytest.mark.asyncio
async def test_over_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(rl, "_is_rate_limit_enabled", lambda: True)

    class FakeRedis:
        async def eval(self, script, n, key, window):
            return 5

    async def _client():
        return FakeRedis()
    monkeypatch.setattr(rl, "connect_redis", _client)

    req = _req("/api/v1/note/list", route_path="/api/v1/note/list")
    dep = rl.rate_limit(limit=3, window=60)(req)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await dep
    assert ei.value.status_code == 429
    assert ei.value.headers.get("Retry-After") == "60"
