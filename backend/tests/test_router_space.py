"""space_router 测试 —— 组织下的空间 CRUD：角色权限（owner/admin/member/outsider）、
笔记加入/移除、文档列表、越权拒绝。

独立 app 挂 space_router，override get_db 为内存 SQLite，audit 打桩；
VectorStoreService 由 conftest mock，文档计数走笔记路径。
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
OUTSIDER = "u-outs-0000-0000-000000000004"


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
    from app.db.db_config import get_db
    from app.router.space_router import space_router
    import app.router.space_router as sp_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    app = FastAPI()
    app.include_router(space_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async def fake_audit(*a, **k):
        return None
    monkeypatch.setattr(sp_mod, "write_audit_log", fake_audit)

    # 种子：组织 + 三角色成员 + 一个空间
    async with factory() as s:
        from app.models.organization import Organization, OrganizationMember
        from app.models.space import Space
        from app.models.note import Note
        s.add(Organization(id="org-1", name="研发部", description="d",
                           owner_id=OWNER))
        for mid, uid, role in [("m-owner", OWNER, "owner"),
                               ("m-admin", ADMIN, "admin"),
                               ("m-member", MEMBER, "member")]:
            s.add(OrganizationMember(id=mid, org_id="org-1", user_id=uid,
                                     username=uid[:5], role=role))
        s.add(Space(id="sp-1", org_id="org-1", name="知识空间",
                    description="desc", created_by=OWNER))
        s.add(Note(id="n-1", user_id=MEMBER, title="成员笔记", content="内容",
                   category="study"))
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
    r = await client.get("/api/v1/space/list?org_id=org-1")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/space/list?org_id=org-1",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


# ---------- 创建 ----------

@pytest.mark.asyncio
async def test_create_space_owner_ok(client):
    r = await client.post("/api/v1/space/create", headers=_auth(OWNER),
                          json={"org_id": "org-1", "name": "新空间",
                                "description": "x"})
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "新空间"
    assert r.json()["data"]["doc_count"] == 0


@pytest.mark.asyncio
async def test_create_space_admin_ok(client):
    r = await client.post("/api/v1/space/create", headers=_auth(ADMIN),
                          json={"org_id": "org-1", "name": "管理员空间"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_create_space_member_forbidden(client):
    r = await client.post("/api/v1/space/create", headers=_auth(MEMBER),
                          json={"org_id": "org-1", "name": "越权空间"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_space_empty_name_400(client):
    r = await client.post("/api/v1/space/create", headers=_auth(OWNER),
                          json={"org_id": "org-1", "name": "   "})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_space_unknown_org_404(client):
    r = await client.post("/api/v1/space/create", headers=_auth(OWNER),
                          json={"org_id": "org-nope", "name": "无组织空间"})
    assert r.status_code == 404


# ---------- 列表 / 详情 ----------

@pytest.mark.asyncio
async def test_list_spaces_member_ok(client):
    r = await client.get("/api/v1/space/list?org_id=org-1",
                         headers=_auth(MEMBER))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["spaces"][0]["name"] == "知识空间"


@pytest.mark.asyncio
async def test_list_spaces_outsider_403(client):
    r = await client.get("/api/v1/space/list?org_id=org-1",
                         headers=_auth(OUTSIDER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_space_member_ok(client):
    r = await client.get("/api/v1/space/sp-1", headers=_auth(MEMBER))
    assert r.status_code == 200
    assert r.json()["data"]["id"] == "sp-1"


@pytest.mark.asyncio
async def test_get_space_outsider_403(client):
    r = await client.get("/api/v1/space/sp-1", headers=_auth(OUTSIDER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_space_missing_404(client):
    r = await client.get("/api/v1/space/sp-nope", headers=_auth(OWNER))
    assert r.status_code == 404


# ---------- 可用笔记 / 文档 ----------

@pytest.mark.asyncio
async def test_available_notes_excludes_existing(client):
    from app.models.space_document import SpaceDocument
    async with client._factory() as s:
        s.add(SpaceDocument(id="sd-1", space_id="sp-1", resource_type="note",
                            resource_id="n-1", added_by=MEMBER))
        await s.commit()

    r = await client.get("/api/v1/space/sp-1/available-notes",
                         headers=_auth(MEMBER))
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_available_notes_shows_own_notes(client):
    from app.models.note import Note
    async with client._factory() as s:
        s.add(Note(id="n-2", user_id=MEMBER, title="未加入", content="c"))
        await s.commit()
    r = await client.get("/api/v1/space/sp-1/available-notes",
                         headers=_auth(MEMBER))
    assert r.status_code == 200
    ids = [n["id"] for n in r.json()["data"]["notes"]]
    assert "n-2" in ids
    assert "n-1" in ids  # n-1 也是 MEMBER 未加入的笔记，应同时出现


@pytest.mark.asyncio
async def test_available_notes_outsider_403(client):
    r = await client.get("/api/v1/space/sp-1/available-notes",
                         headers=_auth(OUTSIDER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_space_documents_member_ok(client):
    from app.models.space_document import SpaceDocument
    async with client._factory() as s:
        s.add(SpaceDocument(id="sd-2", space_id="sp-1", resource_type="note",
                            resource_id="n-1", added_by=MEMBER))
        await s.commit()
    r = await client.get("/api/v1/space/sp-1/documents",
                         headers=_auth(MEMBER))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["documents"][0]["resource_type"] == "note"
    assert data["documents"][0]["title"] == "成员笔记"


@pytest.mark.asyncio
async def test_list_space_documents_outsider_403(client):
    r = await client.get("/api/v1/space/sp-1/documents",
                         headers=_auth(OUTSIDER))
    assert r.status_code == 403


# ---------- 笔记加入/移除 ----------

@pytest.mark.asyncio
async def test_add_note_to_space_member_ok(client):
    r = await client.post("/api/v1/space/sp-1/documents/note/n-1",
                          headers=_auth(MEMBER))
    assert r.status_code == 200
    assert r.json()["data"]["note_id"] == "n-1"


@pytest.mark.asyncio
async def test_add_other_users_note_404(client):
    # 用 OWNER 尝试加入 MEMBER 的笔记
    r = await client.post("/api/v1/space/sp-1/documents/note/n-1",
                          headers=_auth(OWNER))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_note_duplicate_400(client):
    from app.models.space_document import SpaceDocument
    async with client._factory() as s:
        s.add(SpaceDocument(id="sd-3", space_id="sp-1", resource_type="note",
                            resource_id="n-1", added_by=MEMBER))
        await s.commit()
    r = await client.post("/api/v1/space/sp-1/documents/note/n-1",
                          headers=_auth(MEMBER))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_add_note_outsider_403(client):
    r = await client.post("/api/v1/space/sp-1/documents/note/n-1",
                          headers=_auth(OUTSIDER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_remove_document_owner_ok(client):
    from app.models.space_document import SpaceDocument
    async with client._factory() as s:
        s.add(SpaceDocument(id="sd-4", space_id="sp-1", resource_type="note",
                            resource_id="n-1", added_by=MEMBER))
        await s.commit()
    r = await client.delete("/api/v1/space/sp-1/documents/sd-4",
                            headers=_auth(OWNER))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_remove_document_added_by_ok(client):
    from app.models.space_document import SpaceDocument
    async with client._factory() as s:
        s.add(SpaceDocument(id="sd-5", space_id="sp-1", resource_type="note",
                            resource_id="n-1", added_by=MEMBER))
        await s.commit()
    r = await client.delete("/api/v1/space/sp-1/documents/sd-5",
                            headers=_auth(MEMBER))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_remove_document_other_member_forbidden(client):
    from app.models.space_document import SpaceDocument
    async with client._factory() as s:
        s.add(SpaceDocument(id="sd-6", space_id="sp-1", resource_type="note",
                            resource_id="n-1", added_by=OWNER))
        await s.commit()
    r = await client.delete("/api/v1/space/sp-1/documents/sd-6",
                            headers=_auth(MEMBER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_remove_document_missing_404(client):
    r = await client.delete("/api/v1/space/sp-1/documents/sd-nope",
                            headers=_auth(OWNER))
    assert r.status_code == 404


# ---------- 更新 / 删除 ----------

@pytest.mark.asyncio
async def test_update_space_owner_ok(client):
    r = await client.put("/api/v1/space/sp-1", headers=_auth(OWNER),
                         json={"name": "改名空间"})
    assert r.status_code == 200
    got = await client.get("/api/v1/space/sp-1", headers=_auth(OWNER))
    assert got.json()["data"]["name"] == "改名空间"


@pytest.mark.asyncio
async def test_update_space_member_forbidden(client):
    r = await client.put("/api/v1/space/sp-1", headers=_auth(MEMBER),
                         json={"name": "越权改"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_update_space_empty_name_400(client):
    r = await client.put("/api/v1/space/sp-1", headers=_auth(OWNER),
                         json={"name": "  "})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_space_missing_404(client):
    r = await client.put("/api/v1/space/sp-nope", headers=_auth(OWNER),
                         json={"name": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_space_owner_ok(client):
    r = await client.delete("/api/v1/space/sp-1", headers=_auth(OWNER))
    assert r.status_code == 200
    gone = await client.get("/api/v1/space/sp-1", headers=_auth(OWNER))
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_delete_space_member_forbidden(client):
    r = await client.delete("/api/v1/space/sp-1", headers=_auth(MEMBER))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_space_missing_404(client):
    r = await client.delete("/api/v1/space/sp-nope", headers=_auth(OWNER))
    assert r.status_code == 404


# ==================== 直接调用端点函数 ====================
# 规避 aiosqlite await 后 coverage 追踪缺陷：ASGI 路径下 await 后的行不计数，
# 直接调用可让这些行被正确计入。

import json  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.router.space_router import (  # noqa: E402
    create_space, list_spaces, get_space, list_available_notes_for_space,
    list_space_documents, add_note_to_space, remove_space_document,
    update_space, delete_space,
)
from app.router.space_router import SpaceCreate, SpaceUpdate  # noqa: E402
from app.core.exceptions import SpaceNotFoundException, OrganizationNotFoundException  # noqa: E402
from app.models.space_document import SpaceDocument  # noqa: E402


@pytest.mark.asyncio
async def test_direct_create_space_success(client):
    async with client._factory() as db:
        resp = await create_space(SpaceCreate(org_id="org-1", name="直调空间"),
                                  OWNER, db)
        assert resp.status_code == 200
        assert json.loads(resp.body)["data"]["name"] == "直调空间"


@pytest.mark.asyncio
async def test_direct_create_space_empty_name_400(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await create_space(SpaceCreate(org_id="org-1", name="  "), OWNER, db)
        assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_direct_create_space_unknown_org(client):
    async with client._factory() as db:
        with pytest.raises(OrganizationNotFoundException):
            await create_space(SpaceCreate(org_id="org-x", name="x"), OWNER, db)


@pytest.mark.asyncio
async def test_direct_create_space_member_forbidden(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await create_space(SpaceCreate(org_id="org-1", name="x"), MEMBER, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_list_spaces_outsider(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await list_spaces("org-1", OUTSIDER, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_list_spaces_member(client):
    async with client._factory() as db:
        resp = await list_spaces("org-1", MEMBER, db)
        data = json.loads(resp.body)["data"]
        assert data["total"] == 1


@pytest.mark.asyncio
async def test_direct_get_space_missing(client):
    async with client._factory() as db:
        with pytest.raises(SpaceNotFoundException):
            await get_space("sp-x", OWNER, db)


@pytest.mark.asyncio
async def test_direct_get_space_outsider(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await get_space("sp-1", OUTSIDER, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_get_space_member(client):
    async with client._factory() as db:
        resp = await get_space("sp-1", MEMBER, db)
        assert json.loads(resp.body)["data"]["doc_count"] == 0


@pytest.mark.asyncio
async def test_direct_available_notes(client):
    async with client._factory() as db:
        resp = await list_available_notes_for_space("sp-1", MEMBER, db)
        ids = [n["id"] for n in json.loads(resp.body)["data"]["notes"]]
        assert "n-1" in ids


@pytest.mark.asyncio
async def test_direct_documents(client):
    async with client._factory() as db:
        sdoc = SpaceDocument(id="sd-d1", space_id="sp-1", resource_type="note",
                             resource_id="n-1", added_by=MEMBER)
        db.add(sdoc)
        await db.commit()
    async with client._factory() as db:
        resp = await list_space_documents("sp-1", MEMBER, db)
        data = json.loads(resp.body)["data"]
        assert data["total"] == 1
        assert data["documents"][0]["preview"] == "内容"


@pytest.mark.asyncio
async def test_direct_add_note_other_user_404(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await add_note_to_space("sp-1", "n-1", OWNER, db)
        assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_direct_add_note_duplicate_400(client):
    async with client._factory() as db:
        await add_note_to_space("sp-1", "n-1", MEMBER, db)
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await add_note_to_space("sp-1", "n-1", MEMBER, db)
        assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_direct_add_note_success(client):
    async with client._factory() as db:
        resp = await add_note_to_space("sp-1", "n-1", MEMBER, db)
        assert json.loads(resp.body)["data"]["note_id"] == "n-1"


@pytest.mark.asyncio
async def test_direct_remove_document_missing(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await remove_space_document("sp-1", "sd-x", OWNER, db)
        assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_direct_remove_document_forbidden(client):
    async with client._factory() as db:
        sdoc = SpaceDocument(id="sd-d2", space_id="sp-1", resource_type="note",
                             resource_id="n-1", added_by=OWNER)
        db.add(sdoc)
        await db.commit()
        with pytest.raises(HTTPException) as ei:
            await remove_space_document("sp-1", "sd-d2", MEMBER, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_remove_document_success(client):
    async with client._factory() as db:
        sdoc = SpaceDocument(id="sd-d3", space_id="sp-1", resource_type="note",
                             resource_id="n-1", added_by=MEMBER)
        db.add(sdoc)
        await db.commit()
        resp = await remove_space_document("sp-1", "sd-d3", MEMBER, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_update_space_missing(client):
    async with client._factory() as db:
        with pytest.raises(SpaceNotFoundException):
            await update_space("sp-x", SpaceUpdate(name="x"), OWNER, db)


@pytest.mark.asyncio
async def test_direct_update_space_forbidden(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await update_space("sp-1", SpaceUpdate(name="x"), MEMBER, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_update_space_empty_name(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await update_space("sp-1", SpaceUpdate(name=" "), OWNER, db)
        assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_direct_update_space_success(client):
    async with client._factory() as db:
        resp = await update_space("sp-1", SpaceUpdate(name="直调改名",
                                                      description="d"), OWNER, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_delete_space_missing(client):
    async with client._factory() as db:
        with pytest.raises(SpaceNotFoundException):
            await delete_space("sp-x", OWNER, db)


@pytest.mark.asyncio
async def test_direct_delete_space_forbidden(client):
    async with client._factory() as db:
        with pytest.raises(HTTPException) as ei:
            await delete_space("sp-1", MEMBER, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_delete_space_success(client):
    async with client._factory() as db:
        resp = await delete_space("sp-1", OWNER, db)
        assert resp.status_code == 200
