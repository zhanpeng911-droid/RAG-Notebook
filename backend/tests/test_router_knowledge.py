"""knowledge_router + knowledge_service 测试。

Router 层：ASGI 鉴权负路径 + 假 KnowledgeService 驱动编排分支（上传、MD5、
列表/详情、图片、索引状态、v2 上传、删除）；直接调用端点函数补齐 body 覆盖。
Service 层：直接单测 KnowledgeService 各方法（mock VectorStoreService）。
"""
import io
import json

import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.config.validator import get_settings
from app.models.chat_history import Base
from fastapi import UploadFile

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"
USER_B = "u-bbbb-0000-0000-000000000002"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


class FakeKnowledgeService:
    """测试用假服务 —— 覆盖 router 编排分支，不执行真实切片/向量化。"""

    async def handle_add_vector_single(self, file, user_id, space_id=""):
        return "doc.txt"

    async def handle_add_vector_multiple(self, files, user_id, space_id=""):
        return ["a.txt", "b.txt"]

    async def handle_add_vector_multiple_stream(self, files, user_id, space_id=""):
        yield 'data: {"type": "start"}\n\n'
        yield 'data: {"type": "finish"}\n\n'

    async def clean_user_upload(self, user_id):
        return None

    async def handle_clear_user_md5(self, user_id, delete_documents=True):
        return None

    async def handle_delete_single_md5(self, user_id, md5_value, delete_documents=True):
        return True

    async def handle_delete_by_filename(self, user_id, filename, delete_documents=True):
        return True

    async def handle_get_md5_info(self, user_id, md5_value):
        return {"md5": md5_value, "filename": "x.txt", "chunks": 3}

    async def handle_get_all_md5_records(self, user_id):
        return [{"md5": "m1", "filename": "x.txt"}]

    async def handle_get_user_knowledge(self, user_id, space_id=None):
        return [{"id": "d1", "md5": "m1", "filename": "x.txt",
                 "original_filename": "x.txt", "user_id": user_id,
                 "chunk_count": 2, "image_count": 0, "preview": "预览",
                 "created_at": None}]

    async def handle_get_document_detail(self, user_id, filename):
        return {"filename": filename, "content": "详情"}

    async def handle_get_document_chunks(self, user_id, filename):
        return {"filename": filename, "total_chunks": 0, "chunks": []}

    async def handle_get_batch_images(self, user_id, md5):
        return {"md5": md5, "images": {"a.png": "data:image/png;base64,AAA"}}


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client(monkeypatch):
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.knowledge_router import knowledge_router, get_knowledge_service

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    app = FastAPI()
    app.include_router(knowledge_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_knowledge_service] = FakeKnowledgeService

    # document_index_service 相关函数打桩（router 只做编排）
    import app.services.document_index_service as di
    monkeypatch.setattr(di, "clean_user_index_records",
                        async_fake({"deleted_count": 2}))
    monkeypatch.setattr(di, "delete_index_record",
                        async_fake({"success": True, "message": "已删除"}))
    monkeypatch.setattr(di, "get_user_index_status", async_fake([]))
    monkeypatch.setattr(di, "get_embedding_health_status", lambda: {"available": True})
    monkeypatch.setattr(di, "reindex_document",
                        async_fake({"success": True, "message": "已重新索引"}))
    monkeypatch.setattr(di, "save_uploaded_file",
                        async_fake({"filename": "doc.txt", "message": "已保存",
                                    "status": "pending_index"}))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        c._app = app
        yield c
    await engine.dispose()


def async_fake(value):
    async def _f(*a, **k):
        return value
    return _f


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.get("/api/v1/knowledge/list")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/knowledge/list",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


# ---------- 上传 ----------

@pytest.mark.asyncio
async def test_upload_single_ok(client):
    r = await client.post("/api/v1/knowledge/add/single",
                          headers=_auth(USER_A),
                          files={"file": ("t.txt", b"hello", "text/plain")})
    assert r.status_code == 200
    assert "doc.txt" in r.json()["message"]


@pytest.mark.asyncio
async def test_upload_multiple_ok(client):
    r = await client.post("/api/v1/knowledge/add/multiple",
                          headers=_auth(USER_A),
                          files=[("files", ("a.txt", b"a", "text/plain")),
                                 ("files", ("b.txt", b"b", "text/plain"))])
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_upload_stream_ok(client):
    r = await client.post("/api/v1/knowledge/add/multiple/stream",
                          headers=_auth(USER_A),
                          files=[("files", ("a.txt", b"a", "text/plain"))])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")


# ---------- MD5 ----------

@pytest.mark.asyncio
async def test_md5_list_ok(client):
    r = await client.get("/api/v1/knowledge/md5/list", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["total_count"] == 1


@pytest.mark.asyncio
async def test_md5_info_ok(client):
    r = await client.get("/api/v1/knowledge/md5/m1", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["filename"] == "x.txt"


@pytest.mark.asyncio
async def test_clear_md5_ok(client):
    r = await client.delete("/api/v1/knowledge/md5/clear", headers=_auth(USER_A))
    assert r.status_code == 200
    r2 = await client.delete("/api/v1/knowledge/md5/clear?delete_documents=false",
                             headers=_auth(USER_A))
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_delete_single_md5_ok(client):
    r = await client.delete("/api/v1/knowledge/md5/delete/m1",
                            headers=_auth(USER_A))
    assert r.status_code == 200


# ---------- 列表 / 详情 / 切片 / 图片 ----------

@pytest.mark.asyncio
async def test_knowledge_list_ok(client):
    r = await client.get("/api/v1/knowledge/list", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["total_count"] == 1


@pytest.mark.asyncio
async def test_knowledge_list_with_index_status(client):
    r = await client.get("/api/v1/knowledge/list?include_index_status=true",
                         headers=_auth(USER_A))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_document_detail_ok(client):
    r = await client.get("/api/v1/knowledge/detail?filename=x.txt",
                         headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["content"] == "详情"


@pytest.mark.asyncio
async def test_document_chunks_ok(client):
    r = await client.get("/api/v1/knowledge/chunks?filename=x.txt",
                         headers=_auth(USER_A))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_batch_images_ok(client):
    r = await client.get(f"/api/v1/knowledge/images/all/{'a' * 32}",
                         headers=_auth(USER_A))
    assert r.status_code == 200
    assert "a.png" in r.json()["data"]["images"]


# ---------- 索引 / embedding ----------

@pytest.mark.asyncio
async def test_index_status_ok(client):
    r = await client.get("/api/v1/knowledge/index-status", headers=_auth(USER_A))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_embedding_health_ok(client):
    r = await client.get("/api/v1/knowledge/embedding-health",
                         headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["available"] is True


@pytest.mark.asyncio
async def test_reindex_ok(client):
    r = await client.post("/api/v1/knowledge/doc-1/reindex",
                          headers=_auth(USER_A))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_delete_document_v2_ok(client):
    r = await client.delete("/api/v1/knowledge/documents/doc-1",
                            headers=_auth(USER_A))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_delete_by_filename_v1_fallback(client, monkeypatch):
    # v2 路径查不到 → 回退 v1（由 FakeKnowledgeService 返回 True → 200）
    import app.db.db_config as dbc
    import app.repositories.document_index_repository as dir_mod

    monkeypatch.setattr(dbc, "AsyncSessionLocal", client._factory)

    class EmptyRepo:
        def __init__(self, session):
            self.session = session

        async def get_user_documents(self, user_id):
            return []

    monkeypatch.setattr(dir_mod, "DocumentIndexRepository", EmptyRepo)

    r = await client.delete("/api/v1/knowledge/delete/filename?filename=x.txt",
                            headers=_auth(USER_A))
    assert r.status_code == 200


# ---------- v2 上传 ----------

@pytest.mark.asyncio
async def test_add_single_v2_ok(client):
    r = await client.post("/api/v1/knowledge/add/single/v2",
                          headers=_auth(USER_A),
                          files={"file": ("t.txt", b"hello", "text/plain")})
    assert r.status_code == 200
    assert r.json()["data"]["filename"] == "doc.txt"


@pytest.mark.asyncio
async def test_add_multiple_v2_ok(client):
    r = await client.post("/api/v1/knowledge/add/multiple/v2",
                          headers=_auth(USER_A),
                          files=[("files", ("a.txt", b"a", "text/plain")),
                                 ("files", ("b.txt", b"b", "text/plain"))])
    assert r.status_code == 200
    assert r.json()["data"]["total_count"] == 2


@pytest.mark.asyncio
async def test_add_multiple_v2_oversize_400(client, monkeypatch):
    # router 在函数体内 from app.services.knowledge_file_validator import ...
    monkeypatch.setattr("app.services.knowledge_file_validator.validate_total_size",
                        lambda total: "总大小超限")
    r = await client.post("/api/v1/knowledge/add/multiple/v2",
                          headers=_auth(USER_A),
                          files=[("files", ("a.txt", b"a", "text/plain"))])
    assert r.status_code == 400


# ---------- 图片文件服务 ----------

@pytest.mark.asyncio
async def test_serve_image_path_traversal_400(client):
    # httpx 会规范化 URL 中的 ..，路径穿越校验走直接调用验证
    from app.router.knowledge_router import serve_knowledge_image
    with pytest.raises(FE) as ei:
        await serve_knowledge_image("a" * 32, "../evil.png", USER_A)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_serve_image_missing_404(client):
    r = await client.get(f"/api/v1/knowledge/image/{'a' * 32}/nope.png",
                         headers=_auth(USER_A))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_serve_image_invalid_md5_400(client):
    # md5 路径参数必须为 32 位十六进制，否则 400（路径穿越防护回归）
    for bad in ("..%5C..%5C..", "m1", "g" * 32, "a" * 31):
        r = await client.get(f"/api/v1/knowledge/image/{bad}/x.png",
                             headers=_auth(USER_A))
        assert r.status_code == 400, bad


@pytest.mark.asyncio
async def test_batch_images_invalid_md5_400(client):
    from app.router.knowledge_router import serve_batch_images
    with pytest.raises(FE) as ei:
        await serve_batch_images("../evil", USER_A, FakeKnowledgeService())
    assert ei.value.status_code == 400


# ==================== 直接调用端点函数（绕 aiosqlite 追踪缺陷） ====================

from app.router.knowledge_router import (  # noqa: E402
    add_vector_single, clean_user_vectors, clear_user_md5, delete_single_md5,
    get_all_md5_records, get_md5_info, get_user_knowledge_list,
    get_document_detail, get_document_chunks, serve_batch_images,
    reindex_document, delete_document_by_id, add_vector_single_v2,
)
from fastapi import HTTPException as FE  # noqa: E402


def _upl(name="t.txt", content=b"hello"):
    return UploadFile(filename=name, file=io.BytesIO(content))


@pytest.mark.asyncio
async def test_direct_upload_single(client):
    async with client._factory() as db:
        resp = await add_vector_single(_upl(), USER_A, FakeKnowledgeService(), None, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_clean_vectors(client, monkeypatch):
    import app.services.document_index_service as di
    calls = {}
    async def _clean(user_id, space_id=None):
        calls["scoped"] = True
        return {"deleted_count": 1}
    monkeypatch.setattr(di, "clean_user_index_records", _clean)
    resp = await clean_user_vectors(USER_A, "sp-1", FakeKnowledgeService())
    assert resp.status_code == 200
    assert calls.get("scoped")


@pytest.mark.asyncio
async def test_direct_clear_md5(client):
    resp = await clear_user_md5(True, USER_A, FakeKnowledgeService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_delete_single_md5(client):
    resp = await delete_single_md5("m1", True, USER_A, FakeKnowledgeService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_md5_list(client):
    resp = await get_all_md5_records(USER_A, FakeKnowledgeService())
    assert json.loads(resp.body)["data"]["total_count"] == 1


@pytest.mark.asyncio
async def test_direct_md5_info(client):
    resp = await get_md5_info("m1", USER_A, FakeKnowledgeService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_md5_info_missing(client):
    class MissingSvc(FakeKnowledgeService):
        async def handle_get_md5_info(self, user_id, md5_value):
            return None
    with pytest.raises(FE) as ei:
        await get_md5_info("m-x", USER_A, MissingSvc())
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_direct_knowledge_list(client):
    async with client._factory() as db:
        resp = await get_user_knowledge_list(USER_A, FakeKnowledgeService(),
                                             None, False, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_document_detail(client):
    resp = await get_document_detail("x.txt", USER_A, FakeKnowledgeService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_document_chunks(client):
    resp = await get_document_chunks("x.txt", USER_A, FakeKnowledgeService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_batch_images(client):
    resp = await serve_batch_images("a" * 32, USER_A, FakeKnowledgeService())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_reindex_ok(client, monkeypatch):
    import app.services.document_index_service as di
    monkeypatch.setattr(di, "reindex_document",
                        async_fake({"success": True, "message": "已重新索引"}))
    resp = await reindex_document("d1", USER_A)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_reindex_fail_400(client, monkeypatch):
    import app.services.document_index_service as di
    monkeypatch.setattr(di, "reindex_document",
                        async_fake({"success": False, "message": "索引失败"}))
    with pytest.raises(FE) as ei:
        await reindex_document("d1", USER_A)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_direct_delete_document_v2_ok(client, monkeypatch):
    import app.services.document_index_service as di
    monkeypatch.setattr(di, "delete_index_record",
                        async_fake({"success": True, "message": "已删除"}))
    resp = await delete_document_by_id("d1", USER_A)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_add_single_v2_duplicate_409(client, monkeypatch):
    import app.services.document_index_service as di
    monkeypatch.setattr(di, "save_uploaded_file",
                        async_fake({"duplicate_filename": True,
                                    "message": "重名"}))
    async with client._factory() as db:
        with pytest.raises(FE) as ei:
            await add_vector_single_v2(_upl(), USER_A, None, db)
        assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_direct_add_single_v2_duplicate_content(client, monkeypatch):
    import app.services.document_index_service as di
    monkeypatch.setattr(di, "save_uploaded_file",
                        async_fake({"duplicate": True, "filename": "t.txt",
                                    "message": "内容已存在"}))
    async with client._factory() as db:
        resp = await add_vector_single_v2(_upl(), USER_A, None, db)
        assert resp.status_code == 200


# ==================== KnowledgeService 直接单测 ====================
