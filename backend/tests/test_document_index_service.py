"""DocumentIndexService 测试 —— 上传/索引状态机、去重拒绝与删除级联。

数据库走真实 SQLite 内存库；向量库服务、Celery 任务与记录服务在
各自导入点打桩，验证补偿降级路径。
"""
import os
import uuid
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import hashlib

import app.services.document_index_service as dis  # noqa: E402
from app.models.chat_history import Base  # noqa: E402
from app.models.document_index import DocumentIndexStatus  # noqa: E402


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    import app.db.db_config as db_cfg
    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", factory)

    # 数据根目录指向临时目录
    monkeypatch.setattr(dis, "get_data_path", lambda: str(tmp_path))

    # MD5 用真实 hashlib 注入（conftest 将该函数 mock 成 MagicMock）
    def _md5_sync(path):
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    monkeypatch.setattr(dis, "get_file_md5_hex_sync", _md5_sync)

    # 向量库假实例（conftest 将其模块整体 mock，直接替换类属性入口）
    box = {"vectors": NS(delete_calls=[])}
    store_impl = NS()

    async def _default_ok_loader(path, md5=None, user_id=None):
        return [NS(page_content="默认正文", metadata={"source": path})]

    store_impl.get_file_document = _default_ok_loader
    store_impl.split_documents_sync = lambda docs: docs
    store_impl.save_md5_hex_seen = []
    store_impl.vectors_store = NS(
        add_documents=lambda docs: None,
        delete=lambda where=None, ids=None: box["vectors"].delete_calls.append(where),
    )

    async def _save_md5(m, filename=None, original=None, u=None):
        store_impl.save_md5_hex_seen.append((m, filename))

    store_impl.save_md5_hex = _save_md5

    class FakeVSCls:
        def __new__(cls):
            return store_impl

    # conftest 预注册的 mock 模块对象已在 sys.modules，直接替换其类属性
    import sys
    mock_vs = sys.modules["app.rag.vector_store"]
    monkeypatch.setattr(mock_vs, "VectorStoreService", FakeVSCls)

    # 记录服务桩
    import app.services.knowledge_record_service as krs
    record_box = {"deleted": []}

    class FakeRec:
        async def delete_single_md5(self, user_id, md5, delete_documents=True):
            record_box["deleted"].append((user_id, md5))

    monkeypatch.setattr(krs, "KnowledgeRecordService", FakeRec)

    # Celery 任务桩（conftest 已将模块整体 mock）
    import app.tasks.index_task as it_mod
    delay_box = []

    def _delay(doc_id, user_id):
        delay_box.append((doc_id, user_id))
        return NS(id="task-1")

    class FakeTask:
        delay = staticmethod(_delay)

    monkeypatch.setattr(it_mod, "index_document_task", FakeTask())

    yield NS(engine=engine, tmp=tmp_path, store=store_impl,
             vectors=box["vectors"], records=record_box["deleted"],
             delays=delay_box)
    await engine.dispose()


def _upload(filename, content=b"hello"):
    async def read():
        return content

    async def seek(pos):
        return None

    return NS(filename=filename, read=read, seek=seek)


@pytest.fixture(autouse=True)
def healthy_embedding(monkeypatch):
    """上传前置健康检查：本地模型对象存在即视为可用。"""
    class LocalEmbed:
        pass

    import app.utils.factory as fmod
    monkeypatch.setattr(fmod, "embed_model",
                        NS(resolve=lambda: LocalEmbed()))
    dis._embedding_health_cache.update({"available": None, "checked_at": 0})
    yield
    dis._embedding_health_cache.update({"available": None, "checked_at": 0})


# ---------- 纯校验 ----------

def test_validate_content_rejects_oversize():
    from app.services.document_index_service import MAX_FILE_SIZE, \
        validate_uploaded_content
    assert validate_uploaded_content(b"x" * 10, "a.txt") is None
    err = validate_uploaded_content(b"x" * (MAX_FILE_SIZE + 1), "a.txt")
    assert err and "20MB" in err


# ---------- 上传主链路 ----------

@pytest.mark.asyncio
async def test_upload_success_flow_indexes_inline(env):
    out = await dis.save_uploaded_file(_upload("说明.txt"), USER := "u1",
                                       space_id="s1")
    assert out["status"] == "indexed"
    assert out["message"] == "文件上传成功，已完成索引"
    assert env.delays == []                      # 同步成功无需后台任务
    saved_file = os.path.join(env.tmp, "knowledge_files", USER)
    assert any(os.scandir(saved_file))


@pytest.mark.asyncio
async def test_upload_duplicate_content_detected(env):
    await dis.save_uploaded_file(_upload("first.txt", b"SAME"), "u-dupc")
    out2 = await dis.save_uploaded_file(_upload("second.txt", b"SAME"),
                                        "u-dupc")
    assert out2.get("duplicate") is True
    assert out2["status"] == "indexed"


@pytest.mark.asyncio
async def test_upload_same_name_rejected_and_temp_removed(env):
    uid = "u-dupname"
    base = await dis.save_uploaded_file(_upload(f"{uid}.txt", b"C"), uid)
    assert base["status"] == "indexed"

    dup = await dis.save_uploaded_file(_upload(f"{uid}.txt", b"D"), uid)
    assert dup.get("duplicate_filename") is True
    assert dup["filename"] == f"{uid}.txt"
    # 拒绝路径下新物理文件应被清理，目录中只剩第一次那份
    user_dir = os.path.join(env.tmp, "knowledge_files", uid)
    assert len(os.listdir(user_dir)) == 1


@pytest.mark.asyncio
async def test_upload_invalid_type_raises(env):
    with pytest.raises(ValueError):
        await dis.save_uploaded_file(_upload("bad.exe"), "u1")


# ---------- 索引失败的补偿降级 ----------

@pytest.mark.asyncio
async def test_sync_failure_falls_back_to_celery_delay(env):
    async def broken(*a, **k):
        raise RuntimeError("chroma down")
    env.store.get_file_document = broken

    out = await dis.save_uploaded_file(_upload("fail.txt"), "u-sync")
    assert out["status"] == "pending_index"
    assert "chroma down" in out["message"]
    assert len(env.delays) == 1                  # 提交了后台任务


@pytest.mark.asyncio
async def test_sync_index_marks_failed_when_loader_empty(env):
    made = await dis.save_uploaded_file(_upload("empty.txt"), "u-empty")
    doc_id = made["document_id"]

    async def empty_loader(path, md5=None, user_id=None):
        return []
    env.store.get_file_document = empty_loader

    await dis._sync_index(doc_id, "u-empty")

    rows = await dis.get_user_index_status("u-empty")
    row = next(x for x in rows if x["id"] == doc_id)
    assert row["status"] == "index_failed"
    assert row["error_message"] == "文档加载为空"


def _to_failed_and_keep_file(made_doc_id, user_id, tmp_path):
    """把记录转为失败态且确保物理文件仍在，供重试用。"""
    import app.db.db_config as db_cfg
    from app.repositories.document_index_repository import DocumentIndexRepository
    import asyncio as _aio

    async def _mutate():
        async with db_cfg.AsyncSessionLocal() as s:
            repo = DocumentIndexRepository(s)
            await repo.update_status(made_doc_id,
                                     DocumentIndexStatus.INDEX_FAILED)
            await s.commit()
    _aio.get_event_loop_policy()
    import asyncio
    asyncio.run(_mutate()) if False else None
    # 直接同步驱动：pytest-asyncio 下用 wait_for 包一层不可靠，
    # 改由调用方在异步测试体内执行真正的状态迁移。
    raise NotImplementedError("placeholder")


# ---------- 重试路径 ----------

@pytest.mark.asyncio
async def test_reindex_allowed_only_in_failed_or_pending(env):
    made = await dis.save_uploaded_file(_upload("guard.txt"), "u-guard")
    doc_id = made["document_id"]
    # 初始为 indexed：不允许
    res = await dis.reindex_document(doc_id, "u-guard")
    assert res["success"] is False and "不允许" in res["message"]

    # 转 failed 后允许并提交任务
    import app.db.db_config as db_cfg
    from app.repositories.document_index_repository import DocumentIndexRepository
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEX_FAILED)
        await s.commit()

    ok = await dis.reindex_document(doc_id, "u-guard")
    assert ok["success"] is True and "重新索引任务" in ok["message"]
    assert env.delays[-1] == (doc_id, "u-guard")


@pytest.mark.asyncio
async def test_reindex_missing_record_rejected(env):
    res = await dis.reindex_document(str(uuid.uuid4()), "someone")
    assert res == {"success": False, "message": "文档不存在"}


# ---------- 状态查询与删除级联 ----------

@pytest.mark.asyncio
async def test_status_list_shape(env):
    made = await dis.save_uploaded_file(_upload("shape.txt"), "u-shape")
    rows = await dis.get_user_index_status("u-shape")
    row = next(x for x in rows if x["id"] == made["document_id"])
    assert row["filename"] == "shape.txt"
    assert row["status"] == "indexed"
    assert row["created_at"]


@pytest.mark.asyncio
async def test_delete_index_idempotent(env):
    made = await dis.save_uploaded_file(_upload("del.txt", b"DELME"),
                                        "u-del")
    doc_id = made["document_id"]

    out = await dis.delete_index_record(doc_id, "u-del")
    assert out == {"success": True, "message": "文档已删除"}

    again = await dis.delete_index_record(doc_id, "u-del")
    assert again["success"] is True and "幂等" in again["message"]

    rows = await dis.get_user_index_status("u-del")
    assert all(x["id"] != doc_id for x in rows)


@pytest.mark.asyncio
async def test_clean_user_records_counts_and_clears(env):
    await dis.save_uploaded_file(_upload("c1.txt", b"C1"), "u-clean")
    await dis.save_uploaded_file(_upload("c2.txt", b"C2"), "u-clean")

    res = await dis.clean_user_index_records("u-clean")
    assert res["deleted_count"] == 2 and res["success"] is True

    empties = await dis.clean_user_index_records("u-clean")
    assert empties["deleted_count"] == 0 and "没有" in empties["message"]


# ---------- 健康检查缓存 ----------

def test_embedding_health_cache_paths(monkeypatch):
    import app.utils.factory as fmod

    class DashOK:
        def embed_query(self, t):
            return [0.0]

    dis._embedding_health_cache.update({"available": None, "checked_at": 0})
    monkeypatch.setattr(fmod, "embed_model", NS(resolve=lambda: None))
    assert dis._check_embedding_available(force_check=True) is False

    class FakeDashScopeEmbedding:
        def embed_query(self, t):
            return [0.0]

    monkeypatch.setattr(fmod, "embed_model",
                        NS(resolve=lambda: FakeDashScopeEmbedding()))
    assert dis._check_embedding_available(force_check=True) is True
    assert dis.get_embedding_health_status()["available"] is True


# ==================== _sanitize_storage_filename（路径穿越防护回归） ====================

from app.services.document_index_service import _sanitize_storage_filename  # noqa: E402


def test_sanitize_filename_strips_path_components():
    assert _sanitize_storage_filename("a.txt") == "a.txt"
    assert _sanitize_storage_filename(".." + chr(92) + ".." + chr(92) + "evil.txt") == "evil.txt"
    assert _sanitize_storage_filename("../../evil.txt") == "evil.txt"
    assert _sanitize_storage_filename("dir/../../../x.pdf") == "x.pdf"


def test_sanitize_filename_rejects_empty_and_dot_names():
    import pytest
    for bad in ("", "..", ".", ".." + chr(92) + ".."):
        with pytest.raises(ValueError):
            _sanitize_storage_filename(bad)
