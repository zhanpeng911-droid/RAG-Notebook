"""chat_router 测试 —— 会话历史管理：鉴权 + ChatService 桩驱动。

override get_router_service 为假服务，覆盖会话查询/删除/列表与越权 403。
"""
import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt

from app.config.validator import get_settings
from fastapi import HTTPException as FE

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"
USER_B = "u-bbbb-0000-0000-000000000002"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


class FakeChatService:
    async def handle_get_session(self, session_id, user_id):
        return [("问", "答")]

    async def handle_delete_session(self, session_id, user_id):
        return None

    async def handle_get_user_sessions(self, user_id, current_user_id):
        if user_id != current_user_id:
            raise FE(status_code=403, detail="Forbidden")
        return [{"id": "sess-1", "title": "会话"}]


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client():
    from fastapi import FastAPI
    from app.router.chat import chat_router, get_router_service

    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)
    app.dependency_overrides[get_router_service] = FakeChatService

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        yield c


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.get("/api/v1/chat/sessions")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/chat/sessions",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


# ---------- 正路径 ----------

@pytest.mark.asyncio
async def test_get_session_ok(client):
    r = await client.get("/api/v1/chat/session/sess-1", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["history"] == [["问", "答"]]


@pytest.mark.asyncio
async def test_delete_session_ok(client):
    r = await client.delete("/api/v1/chat/session/sess-1", headers=_auth(USER_A))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_all_sessions_ok(client):
    r = await client.get("/api/v1/chat/sessions", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["sessions"][0]["id"] == "sess-1"


@pytest.mark.asyncio
async def test_get_user_sessions_own_ok(client):
    r = await client.get("/api/v1/chat/sessions/" + USER_A, headers=_auth(USER_A))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_user_sessions_other_forbidden(client):
    r = await client.get("/api/v1/chat/sessions/" + USER_B, headers=_auth(USER_A))
    assert r.status_code == 403


# ---------- 直接调用端点函数 ----------

from app.router.chat import (  # noqa: E402
    get_session, delete_session, get_all_sessions, get_user_sessions,
)


@pytest.mark.asyncio
async def test_direct_get_session(client):
    resp = await get_session("sess-1", USER_A, FakeChatService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_delete_session(client):
    resp = await delete_session("sess-1", USER_A, FakeChatService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_get_all_sessions(client):
    resp = await get_all_sessions(USER_A, FakeChatService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_get_user_sessions(client):
    resp = await get_user_sessions(USER_A, USER_A, FakeChatService())
    assert resp.status_code == 200
