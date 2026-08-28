"""auth_utils 直接单测 —— JWT 解析、黑名单检查、当前用户依赖、降级链。

get_current_user_id 等依赖函数直接以 HTTPAuthorizationCredentials 调用。
"""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException as FE
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt as jose_jwt

from app.config.validator import get_settings
from app.utils import auth_utils

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"


class _FakeBlacklistClient:
    """exists 是 async。"""

    def __init__(self, value):
        self._value = value

    async def exists(self, *a, **k):
        return self._value


class _FakeCacheClient:
    """get/delete 是 async。"""

    def __init__(self, value=None):
        self._value = value
        self.deleted = []

    async def get(self, key):
        return self._value

    async def delete(self, key):
        self.deleted.append(key)


def _creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _token(payload):
    return jose_jwt.encode(payload, SECRET, algorithm="HS256")


# ---------- JWT 解析 ----------

def test_decode_jwt_valid():
    tok = _token({"user_id": USER_A, "username": "u"})
    payload = auth_utils.decode_django_jwt(tok)
    assert payload["user_id"] == USER_A


def test_decode_jwt_invalid():
    assert auth_utils.decode_django_jwt("not-a-jwt") is None


# ---------- 黑名单检查 ----------

@pytest.mark.asyncio
async def test_reject_blacklisted_no_jti():
    # 无 jti 直接放行
    await auth_utils._reject_if_blacklisted({"user_id": USER_A})


@pytest.mark.asyncio
async def test_reject_blacklisted_hit(monkeypatch):
    fake_client = _FakeBlacklistClient(1)

    async def _conn():
        return fake_client
    monkeypatch.setattr(auth_utils, "connect_redis", _conn)
    with pytest.raises(FE) as ei:
        await auth_utils._reject_if_blacklisted(
            {"jti": "j1", "user_id": USER_A})
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_reject_blacklisted_miss(monkeypatch):
    fake_client = _FakeBlacklistClient(0)

    async def _conn():
        return fake_client
    monkeypatch.setattr(auth_utils, "connect_redis", _conn)
    await auth_utils._reject_if_blacklisted({"jti": "j1", "user_id": USER_A})


@pytest.mark.asyncio
async def test_reject_blacklisted_redis_error(monkeypatch):
    def _boom():
        raise RuntimeError("redis down")
    monkeypatch.setattr(auth_utils, "connect_redis", _boom)
    with pytest.raises(FE) as ei:
        await auth_utils._reject_if_blacklisted(
            {"jti": "j1", "user_id": USER_A})
    assert ei.value.status_code == 503


# ---------- 当前用户依赖 ----------

@pytest.mark.asyncio
async def test_get_current_user_id_ok():
    tok = _token({"user_id": USER_A, "username": "u"})
    assert await auth_utils.get_current_user_id(_creds(tok)) == USER_A


@pytest.mark.asyncio
async def test_get_current_user_id_missing_user_id():
    tok = _token({"username": "u"})
    with pytest.raises(FE) as ei:
        await auth_utils.get_current_user_id(_creds(tok))
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_id_bad_token():
    with pytest.raises(FE) as ei:
        await auth_utils.get_current_user_id(_creds("junk"))
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_info_ok():
    tok = _token({"user_id": USER_A, "username": "张三"})
    info = await auth_utils.get_current_user_info(_creds(tok))
    assert info == {"user_id": USER_A, "username": "张三"}


@pytest.mark.asyncio
async def test_get_current_user_info_missing_user_id():
    tok = _token({"username": "u"})
    with pytest.raises(FE) as ei:
        await auth_utils.get_current_user_info(_creds(tok))
    assert ei.value.status_code == 401


# ---------- Django API 降级 ----------

@pytest.mark.asyncio
async def test_fetch_user_info_ok(monkeypatch):
    def _fake_get(url, headers=None, timeout=None):
        return NS(status_code=200, json=lambda: {"user_id": USER_A})
    monkeypatch.setattr(auth_utils.requests, "get", _fake_get)
    out = await auth_utils.fetch_user_info_from_django_api("tok", "http://x")
    assert out["user_id"] == USER_A


@pytest.mark.asyncio
async def test_fetch_user_info_non_200(monkeypatch):
    def _fake_get(url, headers=None, timeout=None):
        return NS(status_code=500, json=lambda: {})
    monkeypatch.setattr(auth_utils.requests, "get", _fake_get)
    assert await auth_utils.fetch_user_info_from_django_api("tok", "http://x") is None


@pytest.mark.asyncio
async def test_fetch_user_info_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("网络")
    monkeypatch.setattr(auth_utils.requests, "get", _boom)
    assert await auth_utils.fetch_user_info_from_django_api("tok", "http://x") is None


# ---------- Redis 缓存 + 降级链 ----------

@pytest.mark.asyncio
async def test_get_user_info_redis_hit(monkeypatch):
    import json
    fake_client = _FakeCacheClient(json.dumps({"user_id": USER_A}))

    async def _conn():
        return fake_client
    monkeypatch.setattr(auth_utils, "connect_redis", _conn)
    out = await auth_utils.get_user_info_from_redis(
        USER_A, _creds(_token({"user_id": USER_A})))
    assert out["user_id"] == USER_A


@pytest.mark.asyncio
async def test_get_user_info_redis_miss_fetch(monkeypatch):
    fake_client = _FakeCacheClient(None)

    async def _conn():
        return fake_client
    monkeypatch.setattr(auth_utils, "connect_redis", _conn)
    async def _fetch(t, u):
        return {"user_id": USER_A}
    monkeypatch.setattr(auth_utils, "fetch_user_info_from_django_api", _fetch)
    set_called = []

    async def _set(k, v, expire=3600):
        set_called.append(k)
    monkeypatch.setattr(auth_utils, "set_redis_cache", _set)
    out = await auth_utils.get_user_info_from_redis(
        USER_A, _creds(_token({"user_id": USER_A})))
    assert out["user_id"] == USER_A
    assert set_called


@pytest.mark.asyncio
async def test_get_user_info_redis_unavailable(monkeypatch):
    def _boom():
        raise RuntimeError("redis down")
    monkeypatch.setattr(auth_utils, "connect_redis", _boom)
    async def _fetch(t, u):
        return {"user_id": USER_A}
    monkeypatch.setattr(auth_utils, "fetch_user_info_from_django_api", _fetch)
    out = await auth_utils.get_user_info_from_redis(
        USER_A, _creds(_token({"user_id": USER_A})))
    assert out["user_id"] == USER_A
