"""R2B/R2C/R2D/R3/R6C 回归 —— 执行代次、统一执行服务、读取侧过滤、有界上传、运行回收。

- R2B：claim 递增执行代次；发布要求状态+代次匹配并推进 active_generation；
- R2C：worker 与 HTTP 同步模式共用 execute_index_document（唯一执行实现）；
- R2D：发布后回收旧代次向量；检索候选进入上下文前按可见性过滤；
- R3：上传分块读取并强制 20MB 硬上限；
- R6C：过期 started 运行回收为 timeout。
"""
import uuid
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.document_index_service as dis
import app.tasks.index_task as it
from app.models.chat_history import Base
from app.models.document_index import DocumentIndexStatus
from app.repositories.document_index_repository import DocumentIndexRepository
from app.repositories.agent_run_repository import AgentRunRepository


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    import app.db.db_config as db_cfg
    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", factory)

    import sys
    box = {"delete_calls": [], "add_calls": []}

    def _delete(where=None, ids=None):
        box["delete_calls"].append({"where": where, "ids": ids})

    def _add_documents(docs, **kwargs):
        box["add_calls"].append((docs, kwargs.get("ids")))

    store_impl = NS(
        get_file_document=None,
        split_documents_sync=lambda docs: docs,
        vectors_store=NS(add_documents=_add_documents, delete=_delete),
        save_md5_hex_seen=[],
    )

    async def _save_md5(m, filename=None, original=None, u=None):
        store_impl.save_md5_hex_seen.append((m, filename))

    store_impl.save_md5_hex = _save_md5

    async def _loader(path, md5=None, user_id=None):
        return [NS(page_content="正文", metadata={"source": path})]

    store_impl.get_file_document = _loader

    class FakeVSCls:
        def __new__(cls):
            return store_impl

    monkeypatch.setattr(sys.modules["app.rag.vector_store"],
                        "VectorStoreService", FakeVSCls)

    yield NS(factory=factory, tmp=tmp_path, store=store_impl, box=box)
    await engine.dispose()


async def _seed_doc(env, user_id="u1", status=DocumentIndexStatus.PENDING_INDEX):
    path = str(env.tmp / f"{uuid.uuid4().hex}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("正文")
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        md5 = uuid.uuid4().hex
        doc = await repo.create(user_id, f"{md5}.pdf", f"{md5}-x.pdf", path,
                                md5=md5, status=status)
        await s.commit()
        return doc.id, doc.md5


async def _row(env, doc_id, user_id="u1"):
    async with env.factory() as s:
        return await DocumentIndexRepository(s).get_by_id(doc_id, user_id)


# ---------- R2B：代次生命周期 ----------

@pytest.mark.asyncio
async def test_claim_increments_generation_and_publish_advances_active(env):
    """claim 递增 generation；发布要求代次匹配并推进 active_generation。"""
    doc_id, _ = await _seed_doc(env)

    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        gen1 = await repo.claim_for_indexing(doc_id)
        await s.commit()
        assert gen1 == 1

        # 第二次 claim：状态已是 indexing，抢不到
        assert await repo.claim_for_indexing(doc_id) is None

        # 代次不匹配的发布被拒（迟到/旧 worker 的执行身份不成立）
        assert await repo.publish_indexed(doc_id, chunk_count=5, generation=99) is False
        row = await repo.get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEXING
        assert row.active_generation == 0

        # 匹配代次的发布成功并推进可见代次
        assert await repo.publish_indexed(doc_id, chunk_count=3, generation=gen1) is True
        row = await repo.get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEXED
        assert row.active_generation == 1
        assert row.chunk_count == 3


@pytest.mark.asyncio
async def test_reindex_uses_new_generation_and_cleans_old(env):
    """重新索引走新代次：chunk ID 换代，发布后清理旧代次向量（元数据过滤）。"""
    doc_id, md5 = await _seed_doc(env)

    # 第一次执行
    r1 = await dis.execute_index_document(doc_id, "u1")
    assert r1["status"] == DocumentIndexStatus.INDEXED
    first_ids = env.box["add_calls"][-1][1]
    assert first_ids == [f"u1:{doc_id}:g1:0"]
    row = await _row(env, doc_id)
    assert row.active_generation == 1

    # 重新索引：模拟失败→重试（indexed 状态按产品合同不允许直接重建）
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEX_FAILED)
        await s.commit()
        assert await repo.reset_to_pending_if_failed(doc_id)
        await s.commit()

    r2 = await dis.execute_index_document(doc_id, "u1")
    assert r2["status"] == DocumentIndexStatus.INDEXED
    second_ids = env.box["add_calls"][-1][1]
    assert second_ids == [f"u1:{doc_id}:g2:0"]  # 新代次新 ID
    row = await _row(env, doc_id)
    assert row.active_generation == 2

    # 旧代次清理：按 generation < active_generation 过滤
    cleanups = [c for c in env.box["delete_calls"] if c["where"]]
    assert cleanups, "发布后未清理旧代次向量"
    last = cleanups[-1]["where"]
    assert {"generation": {"$lt": 2}} in last["$and"]
    assert {"md5": md5} in last["$and"]


@pytest.mark.asyncio
async def test_worker_and_sync_share_executor(env, monkeypatch):
    """R2C：worker 与 HTTP 同步模式共用 execute_index_document，无第二套实现。"""
    called = {"n": 0}
    orig = dis.execute_index_document

    async def spy(doc_id, user_id):
        called["n"] += 1
        return await orig(doc_id, user_id)

    monkeypatch.setattr(dis, "execute_index_document", spy)

    doc_id, _ = await _seed_doc(env)
    await it._index_document_async(NS(request=NS(retries=0, max_retries=3),
                                      retry=lambda exc=None: None), doc_id, "u1")
    assert called["n"] == 1

    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEX_FAILED)
        await repo.reset_to_pending_if_failed(doc_id)
        await s.commit()
    await dis._sync_index(doc_id, "u1")
    assert called["n"] == 2


@pytest.mark.asyncio
async def test_executor_missing_file_terminal_and_status_contract(env):
    """缺文件：终态失败（retryable=False），状态与错误信息落库。"""
    doc_id, _ = await _seed_doc(env)
    # 删除物理文件
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        import os
        os.unlink(row.file_path)

    r = await dis.execute_index_document(doc_id, "u1")
    assert r["status"] == DocumentIndexStatus.INDEX_FAILED
    assert r["retryable"] is False
    assert "文件已丢失" in r["error"]


# ---------- R2D：读取侧可见性过滤 ----------

@pytest.mark.asyncio
async def test_retrieval_filter_drops_deleted_and_stale_generation(env, monkeypatch):
    """检索候选过滤：已删文档的切片与旧代次切片不进入上下文。"""
    from app.rag.retrieval_service import RetrievalService

    async def _active(user_id, md5s):
        # M-OK 已发布代次 2；M-DEL 已删除；M-OLD 已重建（可见代次 3）
        return {"M-OK": 2, "M-OLD": 3}

    monkeypatch.setattr(dis, "get_active_document_md5s", _active)

    svc = RetrievalService(user_id="u1")
    docs = [
        NS(page_content="ok", metadata={"md5": "M-OK", "generation": 2}),
        NS(page_content="deleted", metadata={"md5": "M-DEL", "generation": 1}),
        NS(page_content="stale", metadata={"md5": "M-OLD", "generation": 2}),
        NS(page_content="legacy", metadata={"md5": "M-OK"}),  # 存量无代次：放行
        NS(page_content="no-md5", metadata={}),               # 无标识：丢弃
    ]
    kept = await svc._filter_active_documents(docs)
    assert [d.page_content for d in kept] == ["ok", "legacy"]


@pytest.mark.asyncio
async def test_retrieval_filter_fails_closed_on_db_error(env, monkeypatch):
    """可见性查询失败：fail-closed 丢弃全部候选，不让未验证切片进上下文。"""
    from app.rag.retrieval_service import RetrievalService

    async def _boom(user_id, md5s):
        raise RuntimeError("db down")

    monkeypatch.setattr(dis, "get_active_document_md5s", _boom)

    svc = RetrievalService(user_id="u1")
    docs = [NS(page_content="x", metadata={"md5": "M1", "generation": 1})]
    assert await svc._filter_active_documents(docs) == []


@pytest.mark.asyncio
async def test_get_active_document_md5s_only_indexed(env):
    """get_indexed_md5s 只返回 indexed 状态的文档。"""
    doc_id, md5 = await _seed_doc(env)
    active = await dis.get_active_document_md5s("u1", [md5])
    assert active == {}  # pending_index：不可见

    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        gen = await repo.claim_for_indexing(doc_id)
        await repo.publish_indexed(doc_id, chunk_count=1, generation=gen)
        await s.commit()

    active = await dis.get_active_document_md5s("u1", [md5])
    assert active == {md5: gen}

    # 用户隔离
    assert await dis.get_active_document_md5s("u2", [md5]) == {}


# ---------- R3：有界上传读取 ----------

@pytest.mark.asyncio
async def test_bounded_upload_rejects_declared_oversize_before_read(env, monkeypatch):
    """声明大小超限：不进入分块读取即拒绝。"""
    read_calls = {"n": 0}

    class BigFile:
        filename = "big.txt"
        size = dis.MAX_FILE_SIZE + 1

        async def read(self, size=-1):
            read_calls["n"] += 1
            return b"x" * 1024

        async def seek(self, pos):
            return None

    with pytest.raises(ValueError):
        await dis.save_uploaded_file(BigFile(), "u-big", space_id="")
    assert read_calls["n"] == 0


@pytest.mark.asyncio
async def test_bounded_upload_rejects_stream_oversize(env, monkeypatch):
    """声明大小缺失（流式）：分块累计超过硬上限即中止拒绝。"""
    class LyingFile:
        filename = "liar.txt"
        size = None  # 未声明

        def __init__(self):
            self.sent = False

        async def read(self, size=-1):
            if self.sent:
                return b""
            self.sent = True
            return b"x" * (dis.MAX_FILE_SIZE + 1)

        async def seek(self, pos):
            return None

    with pytest.raises(ValueError):
        await dis.save_uploaded_file(LyingFile(), "u-liar", space_id="")


@pytest.mark.asyncio
async def test_bounded_upload_normal_streaming_ok(env, monkeypatch):
    """正常分块流（多块累计）应成功落库并索引。"""
    class StreamFile:
        filename = "stream.txt"
        size = 3 * 1024 * 1024

        def __init__(self):
            self.chunks_sent = 0

        async def read(self, size=-1):
            if self.chunks_sent >= 3:
                return b""
            self.chunks_sent += 1
            return b"A" * (1024 * 1024)

        async def seek(self, pos):
            return None

    import hashlib

    def _md5_sync(path):
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()

    monkeypatch.setattr(dis, "get_file_md5_hex_sync", _md5_sync)
    monkeypatch.setattr(dis, "get_data_path", lambda: str(env.tmp))

    import app.utils.factory as fmod

    class LocalEmbed:
        pass

    monkeypatch.setattr(fmod, "embed_model", NS(resolve=lambda: LocalEmbed()))
    dis._embedding_health_cache.update({"available": None, "checked_at": 0})
    try:
        out = await dis.save_uploaded_file(StreamFile(), "u-stream", space_id="")
    finally:
        dis._embedding_health_cache.update({"available": None, "checked_at": 0})
    assert out["status"] == "indexed"


# ---------- R6C：过期运行回收 ----------

@pytest.mark.asyncio
async def test_reclaim_stale_runs_only_touches_old_started(env):
    """回收只处理超过阈值仍为 started 的运行；终态运行不受影响。"""
    from datetime import datetime, timedelta, timezone
    from app.models.agent_run import AgentRun

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old_cutoff = now - timedelta(minutes=20)

    async with env.factory() as s:
        s.add(AgentRun(id="run-old", user_id="u1", query="q",
                       status="started", created_at=old_cutoff))
        s.add(AgentRun(id="run-fresh", user_id="u1", query="q",
                       status="started", created_at=now - timedelta(minutes=1)))
        s.add(AgentRun(id="run-done", user_id="u1", query="q",
                       status="completed", created_at=old_cutoff))
        await s.commit()

    async with env.factory() as s:
        repo = AgentRunRepository(s)
        reclaimed = await repo.reclaim_stale_runs(now - timedelta(minutes=10))
        await s.commit()
        assert reclaimed == 1

        run_old = await repo.get_run("run-old", "u1")
        assert run_old.status == "timeout"
        assert run_old.completed_at is not None
        assert "回收" in run_old.error_message

        run_fresh = await repo.get_run("run-fresh", "u1")
        assert run_fresh.status == "started"  # 未到期：不回收

        run_done = await repo.get_run("run-done", "u1")
        assert run_done.status == "completed"  # 终态：不触碰


def test_reclaim_task_registered():
    """回收任务必须注册到 Celery include 与 Beat 调度（R6C）。"""
    from pathlib import Path
    celery_path = Path(__file__).resolve().parents[1] / "app" / "tasks" / "celery_app.py"
    source = celery_path.read_text(encoding="utf-8")
    assert "app.tasks.agent_maintenance_task" in source
    assert "reclaim_stale_agent_runs_task" in source
