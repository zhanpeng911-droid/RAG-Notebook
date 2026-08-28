"""user_router 测试 —— 当前用户详情（Redis 降级链打桩）。

get_user_info_from_redis 打桩，覆盖鉴权负路径与正路径。
"""
import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt

from app.config.validator import get_settings

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client(monkeypatch):
    from fastapi import FastAPI
    from app.router.user import user_router
    import app.router.user as u_mod

    async def fake_user_info(user_id, credentials):
        return {"user_id": user_id, "username": "张三", "email": "a@b.c"}

    monkeypatch.setattr(u_mod, "get_user_info_from_redis", fake_user_info)

    app = FastAPI()
    app.include_router(user_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._u_mod = u_mod
        yield c


@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.get("/api/v1/user/detail/")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/user/detail/",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_detail_ok(client):
    r = await client.get("/api/v1/user/detail/", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["username"] == "张三"


# ---------- 直接调用端点函数 ----------

from app.router.user import get_user_info  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials  # noqa: E402


@pytest.mark.asyncio
async def test_direct_detail(client):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    resp = await get_user_info(USER_A, creds)
    assert resp.status_code == 200
