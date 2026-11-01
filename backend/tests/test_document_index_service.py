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
    box = {"vectors": NS(delete_calls=[]), "add_docs_calls": []}

    def _add_docs(docs, **kwargs):
        box["add_docs_calls"].append((docs, kwargs.get("ids")))

    store_impl = NS()

    async def _default_ok_loader(path, md5=None, user_id=None):
        return [NS(page_content="默认正文", metadata={"source": path})]

    store_impl.get_file_document = _default_ok_loader
    store_impl.split_documents_sync = lambda docs: docs
    store_impl.save_md5_hex_seen = []
    store_impl.vectors_store = NS(
        add_documents=_add_docs,
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
             delays=delay_box, add_docs=box["add_docs_calls"])
    await engine.dispose()


def _upload(filename, content=b"hello"):
    """模拟 starlette UploadFile 的分块读取语义（R3 有界读取依赖）。"""
    state = {"done": False}

    async def read(size=-1):
        if state["done"]:
            return b""
        state["done"] = True
        return content

    async def seek(pos):
        return None

    return NS(filename=filename, read=read, seek=seek, size=len(content))


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
async def test_sync_failure_reports_index_failed_and_schedules_retry(env):
    async def broken(*a, **k):
        raise RuntimeError("chroma down")
    env.store.get_file_document = broken

    out = await dis.save_uploaded_file(_upload("fail.txt"), "u-sync")
    # 响应状态必须与 DB 一致：index_failed（不再伪报 pending_index）
    assert out["status"] == "index_failed"
    assert out["retry_scheduled"] is True
    assert len(env.delays) == 1                  # 提交了后台任务
    # 原始 provider 错误不进入响应文案（只进脱敏日志）
    assert "chroma down" not in out["message"]

    rows = await dis.get_user_index_status("u-sync")
    row = next(x for x in rows if x["id"] == out["document_id"])
    assert row["status"] == "index_failed"       # DB 与响应一致


@pytest.mark.asyncio
async def test_upload_reports_failed_when_docs_empty(env):
    async def empty_loader(path, md5=None, user_id=None):
        return []
    env.store.get_file_document = empty_loader

    out = await dis.save_uploaded_file(_upload("empty.txt"), "u-empty2")
    assert out["status"] == "index_failed"
    assert out["retry_scheduled"] is False       # 内容为空不可自动重试
    assert env.delays == []

    rows = await dis.get_user_index_status("u-empty2")
    row = next(x for x in rows if x["id"] == out["document_id"])
    assert row["status"] == "index_failed"
    assert row["error_message"] == "文档加载为空"


@pytest.mark.asyncio
async def test_upload_reports_failed_when_split_empty(env):
    env.store.split_documents_sync = lambda docs: []

    out = await dis.save_uploaded_file(_upload("split.txt"), "u-split")
    assert out["status"] == "index_failed"
    assert out["retry_scheduled"] is False
    assert env.delays == []

    rows = await dis.get_user_index_status("u-split")
    row = next(x for x in rows if x["id"] == out["document_id"])
    assert row["status"] == "index_failed"
    assert row["error_message"] == "文档切片为空"


@pytest.mark.asyncio
async def test_upload_celery_unavailable_no_fake_schedule(env, monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("provider down")
    env.store.get_file_document = broken

    import app.tasks.index_task as it_mod

    def _raise_delay(*a, **k):
        raise RuntimeError("broker down")

    class BoomTask:
        delay = staticmethod(_raise_delay)

    monkeypatch.setattr(it_mod, "index_document_task", BoomTask())

    out = await dis.save_uploaded_file(_upload("fail2.txt"), "u-celery")
    assert out["status"] == "index_failed"
    assert out["retry_scheduled"] is False       # 不伪装成已入队
    assert "后台重试" not in out["message"]


@pytest.mark.asyncio
async def test_sync_index_marks_failed_when_loader_empty(env):
    made = await dis.save_uploaded_file(_upload("empty.txt"), "u-empty")
    doc_id = made["document_id"]

    # 模拟重新索引：回到 pending_index（claim 只认 pending_index，已 indexed 不重复执行）
    import app.db.db_config as db_cfg
    from app.repositories.document_index_repository import DocumentIndexRepository
    async with db_cfg.AsyncSessionLocal() as s:
        await DocumentIndexRepository(s).update_status(
            doc_id, DocumentIndexStatus.PENDING_INDEX)
        await s.commit()

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
    # S03：结果带逐项状态（deleted / deleting / failed）
    assert out == {"success": True, "state": "deleted", "message": "文档已删除"}

    again = await dis.delete_index_record(doc_id, "u-del")
    assert again["success"] is True and "幂等" in again["message"]
    assert again["state"] == "deleted"

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


# ---------- 幂等 / 原子抢占 / 删除状态（批次5B） ----------

@pytest.mark.asyncio
async def test_upload_writes_stable_chunk_ids(env):
    out = await dis.save_uploaded_file(_upload("ids.txt"), "u-ids")
    assert out["status"] == "indexed"
    assert len(env.add_docs) == 1
    _docs, ids = env.add_docs[0]
    assert ids is not None and len(ids) == 1
    # 格式：{user_id}:{doc_id}:v1:{seq}
    assert ids[0].startswith("u-ids:")
    # R2B：chunk ID 携带执行代次（首次 claim 后 generation=1）
    assert ":g1:0" in ids[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,uncertain,expected_execution", [
    ("embedding.documents", False, "closed"),
    ("chroma.upsert", False, "closed"),
    ("chroma.upsert", True, "unknown"),
])
async def test_dependency_failure_keeps_only_uncertain_writer_open(
        env, operation, uncertain, expected_execution):
    import httpx
    from sqlalchemy import select
    import app.db.db_config as db_cfg
    from app.models.document_index import DocumentIndex
    from app.models.index_execution import DocumentIndexExecution
    from app.rag.vector_io import VectorDependencyError
    from app.repositories.document_index_repository import DocumentIndexRepository

    owner = "u-dependency-failure"
    made = await dis.save_uploaded_file(_upload("failure.txt"), owner, process_async=True)
    document_id = made["document_id"]
    original_add = env.store.vectors_store.add_documents

    def fail(*args, **kwargs):
        cause = (httpx.ConnectTimeout("could not connect")
                 if operation == "chroma.upsert" and not uncertain
                 else httpx.ReadTimeout("private source text"))
        raise VectorDependencyError(
            operation, "localhost:11434", cause,
            write_uncertain=uncertain, elapsed_s=1.0)

    env.store.vectors_store.add_documents = fail
    result = await dis.execute_index_document(document_id, owner)
    assert result["status"] == DocumentIndexStatus.INDEX_FAILED
    assert result["retryable"] is True
    assert env.add_docs == []
    async with db_cfg.AsyncSessionLocal() as db:
        execution = (await db.execute(select(DocumentIndexExecution).where(
            DocumentIndexExecution.document_id == document_id,
            DocumentIndexExecution.generation == 1))).scalar_one()
        assert execution.status == expected_execution
        assert (execution.closed_at is not None) == (expected_execution == "closed")
        doc = await db.get(DocumentIndex, document_id)
        assert doc.active_generation == 0  # Failure must never publish partial vectors.
        assert await DocumentIndexRepository(db).reset_to_pending_if_failed(document_id)
        await db.commit()

    env.store.vectors_store.add_documents = original_add
    retried = await dis.execute_index_document(document_id, owner)
    assert retried["status"] == DocumentIndexStatus.INDEXED
    async with db_cfg.AsyncSessionLocal() as db:
        doc = await db.get(DocumentIndex, document_id)
        assert doc.active_generation == 2
        old = (await db.execute(select(DocumentIndexExecution).where(
            DocumentIndexExecution.document_id == document_id,
            DocumentIndexExecution.generation == 1))).scalar_one()
        # Retrying never fabricates proof that the earlier remote mutation stopped.
        assert old.status == expected_execution


@pytest.mark.asyncio
async def test_failure_recovery_commit_error_does_not_persist_half_a_transition(env, monkeypatch):
    from sqlalchemy import select
    import app.db.db_config as db_cfg
    from app.models.document_index import DocumentIndex
    from app.models.index_execution import DocumentIndexExecution
    from app.rag.vector_io import VectorDependencyError

    owner = "u-failure-recovery"
    made = await dis.save_uploaded_file(_upload("recovery.txt"), owner, process_async=True)
    original_factory = db_cfg.AsyncSessionLocal
    stage = {"recovery_commit_failed": False}

    class Session:
        def __init__(self):
            self.inner = original_factory()

        def __getattr__(self, name):
            return getattr(self.inner, name)

        async def __aenter__(self):
            await self.inner.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.inner.__aexit__(*args)

        async def commit(self):
            doc_status = await self.inner.scalar(select(DocumentIndex.status).where(
                DocumentIndex.id == made["document_id"]))
            execution_status = await self.inner.scalar(select(DocumentIndexExecution.status).where(
                DocumentIndexExecution.document_id == made["document_id"]))
            # Intercept the actual recovery transition, independent of session
            # construction order or additional reads elsewhere in the service.
            if doc_status == DocumentIndexStatus.INDEX_FAILED and execution_status == "closed":
                stage["recovery_commit_failed"] = True
                raise OSError("commit did not persist")
            await self.inner.commit()

    def fail_embedding(*args, **kwargs):
        raise VectorDependencyError(
            "embedding.documents", "localhost:11434", RuntimeError("failed embedding"),
            write_uncertain=False, elapsed_s=1.0)

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", Session)
    env.store.vectors_store.add_documents = fail_embedding
    await dis.execute_index_document(made["document_id"], owner)
    assert stage["recovery_commit_failed"] is True
    async with original_factory() as db:
        doc = await db.get(DocumentIndex, made["document_id"])
        execution = (await db.execute(select(DocumentIndexExecution).where(
            DocumentIndexExecution.document_id == doc.id))).scalar_one()
        # Both changes rolled back. The existing lease/reconciliation protocol
        # owns recovery; a failed commit cannot fabricate a stopped writer.
        assert doc.status == DocumentIndexStatus.INDEXING
        assert execution.status == "writing"
        assert execution.closed_at is None


@pytest.mark.asyncio
async def test_claim_for_indexing_only_first_wins(env):
    made = await dis.save_uploaded_file(_upload("claim.txt"), "u-claim")
    doc_id = made["document_id"]
    # 初始为 indexed，先转回 pending_index
    import app.db.db_config as db_cfg
    from app.repositories.document_index_repository import DocumentIndexRepository
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.PENDING_INDEX)
        await s.commit()

    # 两个 worker 同时抢占：只有一个成功（返回执行代次，R2B）
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        first = await repo.claim_for_indexing(doc_id)
        await s.commit()
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        second = await repo.claim_for_indexing(doc_id)
        await s.commit()

    assert first  # 真值代次（此前上传已 claim 过一次）
    assert second is None
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        doc = await repo.get_by_id(doc_id, "u-claim")
        assert doc.status == DocumentIndexStatus.INDEXING


@pytest.mark.asyncio
async def test_sync_index_claim_race_exits_without_indexing(env):
    """已 indexed 的文档再次 _sync_index：claim 失败，不重复写入向量。"""
    made = await dis.save_uploaded_file(_upload("race.txt"), "u-race")
    doc_id = made["document_id"]
    before = len(env.add_docs)

    status = await dis._sync_index(doc_id, "u-race")
    assert status == DocumentIndexStatus.INDEXED
    assert len(env.add_docs) == before  # 未重复写向量


@pytest.mark.asyncio
async def test_delete_vector_failure_keeps_deleting_record_and_retries(env):
    made = await dis.save_uploaded_file(_upload("delkeep.txt"), "u-delkeep")
    doc_id = made["document_id"]

    # 向量删除失败 → 保留 deleting 记录，不删 SQL；重试必须再次尝试清理（F02）
    attempts = []

    def _boom_delete(where=None, ids=None):
        attempts.append(where)
        raise RuntimeError("chroma down")
    env.store.vectors_store.delete = _boom_delete

    out = await dis.delete_index_record(doc_id, "u-delkeep")
    assert out["success"] is False
    assert len(attempts) == 1

    # deleting 重试也执行向量清理（不能只看第二次 success）
    out2 = await dis.delete_index_record(doc_id, "u-delkeep")
    assert out2["success"] is False
    assert len(attempts) == 2

    rows = await dis.get_user_index_status("u-delkeep")
    row = next(x for x in rows if x["id"] == doc_id)
    assert row["status"] == "deleting"

    # 向量恢复后再次删除 → 成功
    env.store.vectors_store.delete = lambda where=None, ids=None: None
    out3 = await dis.delete_index_record(doc_id, "u-delkeep")
    assert out3["success"] is True
    rows2 = await dis.get_user_index_status("u-delkeep")
    assert all(x["id"] != doc_id for x in rows2)


@pytest.mark.asyncio
async def test_publish_indexed_blocked_after_deleting(env):
    """已领取 worker 在文档被删除/deleting 后不得把状态改回 indexed（F04）。"""
    made = await dis.save_uploaded_file(_upload("pub.txt"), "u-pub")
    doc_id = made["document_id"]

    import app.db.db_config as db_cfg
    from app.repositories.document_index_repository import DocumentIndexRepository

    # 模拟 worker 领取（pending → indexing）
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.PENDING_INDEX)
        await s.commit()
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        assert await repo.claim_for_indexing(doc_id)
        await s.commit()

    # 删除先到：置为 deleting
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.DELETING)
        await s.commit()

    # worker 完成向量写后条件发布：被阻止，状态保持 deleting
    async with db_cfg.AsyncSessionLocal() as s:
        repo = DocumentIndexRepository(s)
        ok = await repo.publish_indexed(doc_id, chunk_count=5)
        await s.commit()
        assert ok is False
        doc = await repo.get_by_id(doc_id, "u-pub")
        assert doc.status == DocumentIndexStatus.DELETING


# ---------- 路径安全（批次5B） ----------

def test_safe_delete_rejects_sibling_dir_prefix():
    """/user1 与 /user10 前缀相同但互为兄弟目录：commonpath 必须拒绝。"""
    import os
    import tempfile
    root = tempfile.mkdtemp()
    user1 = os.path.join(root, "user1")
    user10 = os.path.join(root, "user10")
    os.makedirs(user1, exist_ok=True)
    os.makedirs(user10, exist_ok=True)
    victim = os.path.join(user10, "x.txt")
    with open(victim, "w") as f:
        f.write("data")

    # 用 user1 的目录做边界：victim 在 user10，必须拒绝
    assert dis._is_within(os.path.realpath(victim), os.path.realpath(user1)) is False
    assert dis._is_within(os.path.realpath(user1), os.path.realpath(user1)) is True

    # 目录本身不得删除
    assert dis._is_within(os.path.realpath(user1), os.path.realpath(user1)) is True
    assert os.path.exists(victim)  # 未被误删


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
