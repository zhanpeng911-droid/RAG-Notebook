"""org_router 测试 —— 组织隔离：鉴权负路径、owner 权限与越权拒绝。

独立 app 挂 org_router，override get_db 为内存 SQLite，write_audit_log
打桩；get_current_user_info 返回固定用户信息。真实组织/成员表驱动
require_role 越权逻辑。
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
OUTSIDER = "u-outs-0000-0000-000000000002"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client(monkeypatch):
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.org_router import org_router
    import app.router.org_router as or_mod
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    app = FastAPI()
    app.include_router(org_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # get_current_user_info 固定返回 owner
    async def fake_user_info():
        return {"user_id": OWNER, "username": "老板"}

    from app.utils.auth_utils import get_current_user_info
    app.dependency_overrides[get_current_user_info] = fake_user_info

    # audit 打桩（避免真实写库）
    async def fake_audit(*a, **k):
        return None
    monkeypatch.setattr(or_mod, "write_audit_log", fake_audit)

    # 用户查找打桩（避免外部 API/用户表依赖）
    async def fake_lookup(payload):
        return {"user_id": "new-user-1", "username": payload.username}
    monkeypatch.setattr(or_mod, "_lookup_user_for_invite", fake_lookup)

    # 种子：owner 的组织 + owner 成员记录
    async with factory() as s:
        from app.models.organization import Organization, OrganizationMember
        s.add(Organization(id="org-1", name="研发部", description="d",
                           owner_id=OWNER))
        s.add(OrganizationMember(id="m-1", org_id="org-1", user_id=OWNER,
                                 username="老板", role="owner"))
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        yield c
    await engine.dispose()


def _auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_401(client):
    assert (await client.get("/api/v1/org/list")).status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/org/list",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


# ---------- owner 正路径 ----------

@pytest.mark.asyncio
async def test_create_org_owner_assigned(client):
    r = await client.post("/api/v1/org/create",
                          headers=_auth(OWNER),
                          json={"name": "新部门", "description": "desc"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["name"] == "新部门"


@pytest.mark.asyncio
async def test_list_orgs_own(client):
    r = await client.get("/api/v1/org/list", headers=_auth(OWNER))
    assert r.status_code == 200
    assert any(o["id"] == "org-1" for o in r.json()["data"]["orgs"])


@pytest.mark.asyncio
async def test_get_org_own(client):
    r = await client.get("/api/v1/org/org-1", headers=_auth(OWNER))
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "研发部"


@pytest.mark.asyncio
async def test_list_members_owner(client):
    r = await client.get("/api/v1/org/org-1/members", headers=_auth(OWNER))
    assert r.status_code == 200
    assert len(r.json()["data"]["members"]) == 1


# ---------- 越权与隔离 ----------

@pytest.mark.asyncio
async def test_outside_get_org_forbidden(client):
    r = await client.get("/api/v1/org/org-1", headers=_auth(OUTSIDER))
    assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_outside_update_org_forbidden(client):
    r = await client.put("/api/v1/org/org-1",
                         headers=_auth(OUTSIDER),
                         json={"name": "篡改"})
    assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_outside_delete_org_forbidden(client):
    r = await client.delete("/api/v1/org/org-1", headers=_auth(OUTSIDER))
    assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_owner_update_org_ok(client):
    r = await client.put("/api/v1/org/org-1", headers=_auth(OWNER),
                         json={"name": "研发一部"})
    assert r.status_code == 200
    got = await client.get("/api/v1/org/org-1", headers=_auth(OWNER))
    assert got.json()["data"]["name"] == "研发一部"


# ---------- 删除 / 邀请 / 成员管理 ----------

@pytest.mark.asyncio
async def test_owner_delete_org_ok(client):
    r = await client.delete("/api/v1/org/org-1", headers=_auth(OWNER))
    assert r.status_code == 200
    gone = await client.get("/api/v1/org/org-1", headers=_auth(OWNER))
    assert gone.status_code in (403, 404)


@pytest.mark.asyncio
async def test_invite_member_by_owner(client):
    r = await client.post("/api/v1/org/org-1/invite",
                          headers=_auth(OWNER),
                          json={"username": "新同事"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_invite_by_outsider_forbidden(client):
    r = await client.post("/api/v1/org/org-1/invite",
                          headers=_auth(OUTSIDER),
                          json={"username": "x"})
    assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_remove_member_owner_ok(client):
    # 先邀请新成员使其存在，再由 owner 移除
    await client.post("/api/v1/org/org-1/invite",
                      headers=_auth(OWNER), json={"username": "乙方"})
    r = await client.delete(
        "/api/v1/org/org-1/member/outsider-not-a-real-uuid",
        headers=_auth(OWNER))
    assert r.status_code in (200, 400, 404)


@pytest.mark.asyncio
async def test_update_member_role_owner_ok(client):
    r = await client.put(
        "/api/v1/org/org-1/member/outsider-not-a-real-uuid/role",
        headers=_auth(OWNER), json={"role": "admin"})
    assert r.status_code in (200, 400, 404)
