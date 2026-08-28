"""audit_router 测试 —— 审计日志查询权限：仅 owner/admin 可查。

独立 app 挂 audit_router，override get_db 为内存 SQLite，种子组织成员与审计日志。
"""
import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.config.validator import get_settings
from app.models.chat_history import Base

SECRET = get_settings().SECRET_KEY
OWNER = "u-owner-0000-0000-000000000001"
ADMIN = "u-admin-0000-0000-000000000002"
MEMBER = "u-memb-0000-0000-000000000003"


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
async def client():
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.audit_router import audit_router

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    app = FastAPI()
    app.include_router(audit_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 种子：组织成员 + 审计日志
    async with factory() as s:
        from app.models.organization import OrganizationMember
        from app.models.audit_log import AuditLog
        s.add(OrganizationMember(id="m1", org_id="org-1", user_id=OWNER,
                                 role="owner"))
        s.add(OrganizationMember(id="m2", org_id="org-1", user_id=ADMIN,
                                 role="admin"))
        s.add(OrganizationMember(id="m3", org_id="org-1", user_id=MEMBER,
                                 role="member"))
        s.add(AuditLog(id="a1", org_id="org-1", user_id=OWNER, action="create",
                       resource_type="note", resource_id="n1",
                       detail={"title": "x"}))
        s.add(AuditLog(id="a2", org_id="org-1", user_id=ADMIN, action="update",
                       resource_type="space", resource_id="sp1"))
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        yield c
    await engine.dispose()


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.get("/api/v1/audit/logs")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/audit/logs",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


# ---------- 权限 ----------

@pytest.mark.asyncio
async def test_logs_member_forbidden(client):
    r = await client.get("/api/v1/audit/logs", headers=_auth(MEMBER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_logs_member_with_org_forbidden(client):
    r = await client.get("/api/v1/audit/logs?org_id=org-1",
                         headers=_auth(MEMBER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_logs_owner_ok(client):
    r = await client.get("/api/v1/audit/logs", headers=_auth(OWNER))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 2
    assert len(data["logs"]) == 2


@pytest.mark.asyncio
async def test_logs_filter_action(client):
    r = await client.get("/api/v1/audit/logs?action=create",
                         headers=_auth(OWNER))
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["logs"][0]["action"] == "create"


@pytest.mark.asyncio
async def test_logs_filter_keyword(client):
    r = await client.get("/api/v1/audit/logs?keyword=update",
                         headers=_auth(OWNER))
    data = r.json()["data"]
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_logs_org_filter_ok(client):
    r = await client.get("/api/v1/audit/logs?org_id=org-1",
                         headers=_auth(ADMIN))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_logs_pagination(client):
    r = await client.get("/api/v1/audit/logs?page=1&page_size=1",
                         headers=_auth(OWNER))
    data = r.json()["data"]
    assert len(data["logs"]) == 1
    assert data["page_size"] == 1


@pytest.mark.asyncio
async def test_stats_owner_ok(client):
    r = await client.get("/api/v1/audit/stats?org_id=org-1",
                         headers=_auth(OWNER))
    assert r.status_code == 200
    assert r.json()["data"]["stats"]["create"] == 1


@pytest.mark.asyncio
async def test_stats_member_forbidden(client):
    r = await client.get("/api/v1/audit/stats?org_id=org-1",
                         headers=_auth(MEMBER))
    assert r.status_code == 403


# ---------- 直接调用端点函数 ----------

from app.router.audit_router import get_audit_logs, get_audit_stats  # noqa: E402
from fastapi import HTTPException as FE  # noqa: E402


@pytest.mark.asyncio
async def test_direct_logs_owner(client):
    async with client._factory() as db:
        resp = await get_audit_logs(None, None, None, 1, 20, OWNER, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_logs_member_403(client):
    async with client._factory() as db:
        with pytest.raises(FE) as ei:
            await get_audit_logs(None, None, None, 1, 20, MEMBER, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_stats_owner(client):
    async with client._factory() as db:
        resp = await get_audit_stats("org-1", OWNER, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_stats_member_403(client):
    async with client._factory() as db:
        with pytest.raises(FE) as ei:
            await get_audit_stats("org-1", MEMBER, db)
        assert ei.value.status_code == 403
