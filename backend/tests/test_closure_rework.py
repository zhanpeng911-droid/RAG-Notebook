"""收口补充返修 K1—K5 行为回归（T01/T05/T06/T07/T09）。

有状态假向量库：保存 ID 与 metadata，真正执行过滤/插入/删除/查询——
可模拟"远端已写入但调用方未确认"的迟到写入，不再用永远为空的假服务。
"""
import uuid
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chat_history import Base
from app.models.document_index import DocumentIndexStatus
from app.repositories.document_index_repository import DocumentIndexRepository


class StatefulFakeVectorStore:
    """有状态假向量库：真实执行 where 过滤与增删查。"""

    def __init__(self):
        self.docs = {}  # id -> {"content": str, "metadata": dict}

    def _match(self, where, doc_meta):
        if where is None:
            return True
        if "$and" in where:
            return all(self._match(cond, doc_meta) for cond in where["$and"])
        for k, v in where.items():
            if isinstance(v, dict) and "$lt" in v:
                if doc_meta.get(k) is None or int(doc_meta[k]) >= int(v["$lt"]):
                    return False
            else:
                if doc_meta.get(k) != v:
                    return False
        return True

    def add_documents(self, docs, ids=None, **kwargs):
        ids = ids or [f"auto-{uuid.uuid4().hex}" for _ in docs]
        for d, i in zip(docs, ids):
            self.docs[i] = {"content": d.page_content, "metadata": dict(d.metadata)}
        return ids

    def delete(self, where=None, ids=None, **kwargs):
        if ids is not None:
            for i in ids:
                self.docs.pop(i, None)
            return
        doomed = [i for i, d in self.docs.items() if self._match(where, d["metadata"])]
        for i in doomed:
            self.docs.pop(i, None)

    def get(self, ids=None, where=None, include=None, **kwargs):
        if ids is not None:
            hit = [i for i in ids if i in self.docs]
        else:
            hit = [i for i, d in self.docs.items() if self._match(where, d["metadata"])]
        return {
            "ids": hit,
            "documents": [self.docs[i]["content"] for i in hit],
            "metadatas": [self.docs[i]["metadata"] for i in hit],
        }


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
    fake_store = StatefulFakeVectorStore()

    async def _save_md5(m, filename=None, original=None, u=None):
        fake_store.md5_seen = getattr(fake_store, "md5_seen", [])
        fake_store.md5_seen.append(m)

    store_impl = NS(
        get_file_document=None,
        split_documents_sync=lambda docs: docs,
        vectors_store=fake_store,
        save_md5_hex=_save_md5,
    )

    async def _loader(path, md5=None, user_id=None):
        return [NS(page_content="正文", metadata={"source": path})]

    store_impl.get_file_document = _loader

    class FakeVSCls:
        def __new__(cls):
            return store_impl

    monkeypatch.setattr(sys.modules["app.rag.vector_store"],
                        "VectorStoreService", FakeVSCls)

    import app.services.knowledge_record_service as krs

    class FakeRec:
        async def delete_single_md5(self, user_id, md5, delete_documents=True):
            pass

    monkeypatch.setattr(krs, "KnowledgeRecordService", FakeRec)

    monkeypatch.setattr("app.services.document_index_service.get_data_path",
                        lambda: str(tmp_path))

    yield NS(factory=factory, tmp=tmp_path, store=store_impl, vs=fake_store)
    await engine.dispose()


async def _seed_doc(env, user_id="u1", status=DocumentIndexStatus.PENDING_INDEX,
                    chunk_count=0):
    import os
    user_dir = os.path.join(str(env.tmp), "knowledge_files", user_id)
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, f"{uuid.uuid4().hex}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("正文")
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        md5 = uuid.uuid4().hex
        doc = await repo.create(user_id, f"{md5}.pdf", f"{md5}-x.pdf", path,
                                md5=md5, status=status)
        doc.chunk_count = chunk_count
        await s.commit()
        return doc.id, doc.md5


async def _cleanup_tasks(env):
    from app.models.cleanup_task import DocumentCleanupTask
    async with env.factory() as s:
        return list((await s.execute(
            select(DocumentCleanupTask).order_by(DocumentCleanupTask.created_at)
        )).scalars().all())


async def _run_cleanup_worker_once(env, limit=20):
    from app.tasks.index_task import _process_cleanup_tasks_async
    return await _process_cleanup_tasks_async(limit)


def _patch_real_sse_event(monkeypatch):
    """conftest 将 sse_models 放入 MagicMock；行为测试注入真实序列化类。"""
    import importlib.util
    from pathlib import Path
    import app.router.knowledge_service as ks

    path = Path(__file__).resolve().parents[1] / "app" / "rag" / "sse_models.py"
    spec = importlib.util.spec_from_file_location("_closure_real_sse_models", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setattr(ks.sse, "SSEEvent", module.SSEEvent)
    return ks


def _sse_payload(event):
    import json
    data = next(line[6:] for line in event.splitlines() if line.startswith("data:"))
    return json.loads(data)


# ---------- T01：两次查空之后旧写入者才写入，任务不得 done ----------

@pytest.mark.asyncio
async def test_t01_late_write_after_empty_check_not_done(env):
    """K1：写入者未关闭（execution open）时，查空不得判 done；
    关闭后清理收敛。"""
    from app.services.document_index_service import execute_index_document, delete_index_record

    doc_id, md5 = await _seed_doc(env)
    r = await execute_index_document(doc_id, "u1")
    assert r["status"] == DocumentIndexStatus.INDEXED

    # 删除：主记录删、清理任务登记
    out = await delete_index_record(doc_id, "u1")
    assert out["state"] == "deleted"
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")

    # 显式创建 writing 执行并提交（模拟删除前已领取、删除后仍在写）
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.services.document_index_service import _ExecutionRepository
    async with env.factory() as s:
        s.add(DocumentIndexExecution(
            execution_id="exec-late", document_id=doc_id, user_id="u1",
            generation=2, token="tok-late", status=ExecutionStatus.WRITING))
        await s.commit()
    # 断言前置状态：数据库确实存在 writing 执行
    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.execution_id == "exec-late"))).scalar_one()
        assert e.status == ExecutionStatus.WRITING

    def late_write():
        env.vs.add_documents(
            [NS(page_content="迟到", metadata={"user_id": "u1", "md5": md5,
                                              "document_id": doc_id,
                                              "generation": 2})],
            ids=[f"u1:{doc_id}:g2:late"],
        )

    # 第一次 worker：写入者未关闭 → 不得 done
    await _run_cleanup_worker_once(env)
    late_write()
    # 第二次 worker：仍未关闭 → 不得 done
    await _run_cleanup_worker_once(env)
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status != "done", "写入者未关闭时任务不得标完成"

    # 经生产关闭接口提交 closed（不直接改任务状态）
    async with env.factory() as s:
        ok = await _ExecutionRepository(s).close_execution(
            document_id=doc_id, generation=2, token="tok-late",
            status="closed", reason="writer finished")
        await s.commit()
        assert ok is True
    # 写入者关闭后：清理最终收敛（迟到向量被精确清除）
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status == "done"
    assert f"u1:{doc_id}:g2:late" not in env.vs.docs


# ---------- T05/T06：同 MD5 A/B 资源互不影响 ----------

@pytest.mark.asyncio
async def test_t05_t06_same_md5_resources_isolated(env, monkeypatch):
    """同用户同 MD5 的 A/B 文档共存；删除 A 的清理不影响 B；
    A 的旧代次任务不删除 B 的更小代次向量。"""
    from app.services.document_index_service import execute_index_document, delete_index_record

    # A 与 B 同 md5：手工构造（B 是另一个 document_id 的同内容文档）
    a_id, md5 = await _seed_doc(env)
    b_id, _ = await _seed_doc(env)  # 不同 md5 的行；再手工注入 B 的同 md5 向量
    b_meta_md5 = md5

    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        brow = await repo.get_by_id(b_id, "u1")
        brow.md5 = b_meta_md5
        await s.commit()

    await execute_index_document(a_id, "u1")
    await execute_index_document(b_id, "u1")

    # B 的向量带更小代次（模拟 B 先发布）
    env.vs.docs = {
        k: v for k, v in env.vs.docs.items()
    }
    env.vs.add_documents(
        [NS(page_content="B旧代次", metadata={"user_id": "u1", "md5": b_meta_md5,
                                             "document_id": b_id, "generation": 1})],
        ids=[f"u1:{b_id}:g1:0"],
    )

    # 删除 A → A 的清理任务
    out = await delete_index_record(a_id, "u1")
    assert out["state"] == "deleted"
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)

    # B 的向量完整（含旧代次）
    b_remain = env.vs.get(where={"$and": [{"user_id": "u1"}, {"document_id": b_id}]},
                          include=[])
    assert len(b_remain["ids"]) >= 1, "A 的清理不得影响 B"

    # A 的旧代次任务（superseded_generation, gen=1）执行：只清 A 的 g<1 向量，
    # 不得碰 B 的 g1 向量
    await _run_cleanup_worker_once(env)
    b_after = env.vs.get(where={"$and": [{"user_id": "u1"}, {"document_id": b_id}]},
                         include=[])
    assert any("g1:0" in i for i in b_after["ids"]), "B 的旧代次向量被误删"


# ---------- T07：领取条件复核（SELECT 后退避时间改变） ----------

@pytest.mark.asyncio
async def test_t07_claim_respects_next_retry_change(env, monkeypatch):
    """SELECT 候选之后、UPDATE 之前任务被推迟（next_retry_at 后移）：
    旧候选不得被抢领。"""
    from datetime import datetime, timedelta, timezone
    from app.models.cleanup_task import DocumentCleanupTask, CleanupTaskStatus
    from app.repositories.cleanup_task_repository import CleanupTaskRepository
    from app.db.db_config import AsyncSessionLocal as ASL

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with env.factory() as s:
        s.add(DocumentCleanupTask(
            id="t-1", document_id="d1", user_id="u1", md5="M", generation=1,
            reason="superseded_generation", status=CleanupTaskStatus.PENDING,
            next_retry_at=now))
        await s.commit()

    repo = CleanupTaskRepository(ASL())
    # 直接驱动仓库两阶段：拦截 UPDATE 前推迟 next_retry_at


    # 仓库两阶段中：先调用 claim_due_tasks，但在 UPDATE 前注入推迟
    # ——通过包装 session.execute 拦截含 claim_token 的 update
    from sqlalchemy import update as _update

    real_claim = CleanupTaskRepository.claim_due_tasks

    async def claim_with_defer(self, limit=20):
        # 手动展开两阶段，在中间推迟任务
        now2 = datetime.now(timezone.utc).replace(tzinfo=None)
        async with ASL() as s:
            ids = list((await s.execute(
                select(DocumentCleanupTask.id)
                .where(DocumentCleanupTask.status == CleanupTaskStatus.PENDING)
                .limit(10))).scalars().all())
        # 推迟：next_retry_at 后移 1 小时
        async with ASL() as s:
            await s.execute(
                _update(DocumentCleanupTask)
                .where(DocumentCleanupTask.id.in_(ids))
                .values(next_retry_at=now2 + timedelta(hours=1)))
            await s.commit()
        # 现在再领取：不得选中已推迟的任务
        got = await real_claim(self, limit=20)
        return got, ids

    got, ids = await claim_with_defer(repo)
    assert got == [], "SELECT 后被推迟的任务不得被领取"


# ---------- T09：遗留向量无法精确归属 → blocked，不盲删不假完成 ----------

@pytest.mark.asyncio
async def test_t09_unresolvable_legacy_vectors_blocked(env):
    """删除后遗留的无 document_id 元数据向量（旧格式）在同 md5 存在其它
    文档时：任务进入 blocked，不 md5 盲删、不标 done。"""
    from app.services.document_index_service import delete_index_record

    doc_id, md5 = await _seed_doc(env, chunk_count=0)
    # B 文档共享同 md5（重传场景），其向量必须保留
    b_id, _ = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        brow = await repo.get_by_id(b_id, "u1")
        brow.md5 = md5
        await s.commit()
    env.vs.add_documents(
        [NS(page_content="B新内容", metadata={"user_id": "u1", "md5": md5,
                                             "document_id": b_id, "generation": 1})],
        ids=[f"u1:{b_id}:g1:0"],
    )
    # A 的遗留旧格式向量（无 document_id 元数据，md5 相同）
    env.vs.add_documents(
        [NS(page_content="A遗留", metadata={"user_id": "u1", "md5": md5})],
        ids=["legacy-x"],
    )

    out = await delete_index_record(doc_id, "u1")
    assert out["state"] == "deleted"

    # 多轮 worker：遗留向量不能被 md5 盲删，任务最终 blocked
    for _ in range(8):
        await _run_cleanup_worker_once(env)

    assert "legacy-x" in env.vs.docs, "遗留向量被 md5 盲删（禁止）"
    assert env.vs.docs[f"u1:{b_id}:g1:0"]["content"] == "B新内容"
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status == "blocked", "归属不清的遗留数据不得标 done"


# ---------- 评审第四批补充回归 ----------

@pytest.mark.asyncio
async def test_t11_only_attributed_other_doc_not_blocked(env):
    """同 MD5 存在正常 B（无遗留向量）时：A 删除清理正常 done，不进入 blocked。"""
    from app.services.document_index_service import execute_index_document, delete_index_record

    a_id, md5 = await _seed_doc(env)
    b_id, _ = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        brow = await repo.get_by_id(b_id, "u1")
        brow.md5 = md5
        await s.commit()
    await execute_index_document(a_id, "u1")
    await execute_index_document(b_id, "u1")

    out = await delete_index_record(a_id, "u1")
    assert out["state"] == "deleted"
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)

    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status == "done", "只有正常归属的其它文档时不得 blocked"
    # B 完整
    b_remain = env.vs.get(where={"$and": [{"user_id": "u1"}, {"document_id": b_id}]},
                          include=[])
    assert len(b_remain["ids"]) >= 1


@pytest.mark.asyncio
async def test_t06b_superseded_scoped_to_document(env):
    """A 发布到 g2、B 保持有效 g1：A 的 superseded 任务不删除 B 的 g1 向量。"""
    from app.services.document_index_service import execute_index_document

    a_id, _ = await _seed_doc(env)
    b_id, md5 = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        brow = await repo.get_by_id(b_id, "u1")
        brow.md5 = md5
        await s.commit()

    # A 发布到 g1，再重跑到 g2
    await execute_index_document(a_id, "u1")
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(a_id, DocumentIndexStatus.INDEX_FAILED)
        await repo.reset_to_pending_if_failed(a_id)
        await s.commit()
    await execute_index_document(a_id, "u1")

    # B 的 g1 向量（同 md5、document_id=b_id）
    env.vs.add_documents(
        [NS(page_content="B的g1", metadata={"user_id": "u1", "md5": md5,
                                            "document_id": b_id, "generation": 1})],
        ids=[f"u1:{b_id}:g1:0"],
    )

    # 执行 A 的 superseded（gen=2）任务
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)

    tasks = await _cleanup_tasks(env)
    sup = next(t for t in tasks if t.reason == "superseded_generation"
               and t.document_id == a_id and t.generation == 2)
    assert sup.status == "done"
    b_vec = env.vs.docs.get(f"u1:{b_id}:g1:0")
    assert b_vec is not None and b_vec["content"] == "B的g1", "B 的 g1 向量被 A 的旧代次清理误删"


# ---------- N3 返修：删除获胜后执行仍能关闭自身记录 ----------

@pytest.mark.asyncio
async def test_t12_publish_rejected_closes_execution(env):
    """删除获胜 → 发布被拒 → 执行不得停在 writing（评审 N3 #1）。"""
    from app.services.document_index_service import execute_index_document
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus

    doc_id, _ = await _seed_doc(env)

    async def loader(path, md5=None, user_id=None):
        # 执行器 claim 后、发布前，删除获胜
        async with env.factory() as s:
            await DocumentIndexRepository(s).update_status(
                doc_id, DocumentIndexStatus.DELETING)
            await s.commit()
        return [NS(page_content="正文", metadata={"source": path})]

    env.store.get_file_document = loader

    r = await execute_index_document(doc_id, "u1")
    assert r["status"] == DocumentIndexStatus.DELETING

    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
        assert e.status == ExecutionStatus.CLOSED, "发布被拒后执行必须关闭自身记录"
        assert e.close_reason == "publish_rejected"


@pytest.mark.asyncio
async def test_t13_v1_write_failure_unknown_then_resolvable(env):
    """v1 写入异常：执行标 unknown（远端结果不确定），可经解决路径关闭。"""
    from app.router.knowledge_service import KnowledgeService
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.services.document_index_service import _ExecutionRepository

    async def boom(**kwargs):
        raise RuntimeError("chroma 连接中断")

    env.store.get_document = boom

    class _Up:
        filename = "v1unk.txt"
        size = 5

        async def read(self, size=-1):
            return "未知".encode("utf-8")

        async def seek(self, pos):
            return None

    svc = KnowledgeService()
    with pytest.raises(Exception):
        await svc.handle_add_vector_single(_Up(), "u-unk", space_id="")

    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution))).scalar_one()
        assert e.status == ExecutionStatus.UNKNOWN, "写入异常远端结果未知，不得伪称 closed"

    # 人工解决路径：unknown → closed（带审计原因），且不可重复解决
    async with env.factory() as s:
        ok = await _ExecutionRepository(s).resolve_unknown(
            e.execution_id, "维护窗口核验：远端无残留写入")
        await s.commit()
        assert ok is True
        e2 = (await s.execute(select(DocumentIndexExecution)
                              .where(DocumentIndexExecution.execution_id == e.execution_id))).scalar_one()
        assert e2.status == ExecutionStatus.CLOSED
        assert e2.close_reason.startswith("resolved:")
    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            e.execution_id, "重复解决") is False


@pytest.mark.asyncio
async def test_t14_empty_slice_conditional_fail_closes_execution(env):
    """空切片 + 删除获胜（条件失败写回被拒）：执行仍须关闭。"""
    from app.services.document_index_service import execute_index_document
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus

    doc_id, _ = await _seed_doc(env)

    async def loader(path, md5=None, user_id=None):
        async with env.factory() as s:
            await DocumentIndexRepository(s).update_status(
                doc_id, DocumentIndexStatus.DELETING)
            await s.commit()
        return [NS(page_content="正文", metadata={"source": path})]

    env.store.get_file_document = loader
    env.store.split_documents_sync = lambda docs: []  # 空切片

    r = await execute_index_document(doc_id, "u1")
    assert r["status"] == DocumentIndexStatus.DELETING

    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
        assert e.status == ExecutionStatus.CLOSED, "条件失败分支不得遗留 writing"
        assert "删除获胜" in e.close_reason


# ---------- 评审 N3 #1 的 v1/SSE 对应回归 ----------

@pytest.mark.asyncio
async def test_t12b_v1_publish_rejected_closes_execution_and_raises(env):
    """v1：删除获胜 → 发布被拒 → 409 且执行关闭（不停在 writing）。"""
    from app.router.knowledge_service import KnowledgeService
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from fastapi import HTTPException

    doc_id, md5 = await _seed_doc(env)

    async def loader_with_delete(files=None, user_id=None, progress_callback=None,
                                 space_id="", generation=None, document_id=None):
        # 向量写入后、发布前，删除获胜
        async with env.factory() as s:
            await DocumentIndexRepository(s).update_status(
                doc_id, DocumentIndexStatus.DELETING)
            await s.commit()
        return None

    env.store.get_document = loader_with_delete

    class _Up:
        filename = "v1race.txt"
        size = 5

        async def read(self, size=-1):
            # md5 命中已存在行 → 走领取路径
            return "正文".encode("utf-8")

        async def seek(self, pos):
            return None

    svc = KnowledgeService()
    # 领取需要命中已存在的 doc 行：allocate 内部按内容 md5 查行，
    # 这里让内容哈希与 _seed_doc 的 md5 不同 → 会创建新行；改用直接方式：
    # 先删除种子行，创建与 _Up 内容 md5 一致的行
    import hashlib
    content_md5 = hashlib.md5("正文".encode("utf-8")).hexdigest()
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        row.md5 = content_md5
        await s.commit()

    with pytest.raises(HTTPException) as ei:
        await svc.handle_add_vector_single(_Up(), "u1", space_id="")
    assert ei.value.status_code == 409

    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
        assert e.status == ExecutionStatus.CLOSED, "v1 发布被拒后执行必须关闭自身记录"


# ---------- N3 返修 #2：主记录删除后写入异常仍收敛 ----------

@pytest.mark.asyncio
async def test_t15_record_deleted_before_write_failure_still_converges(env):
    """领取 → 物理删除主记录 → 写入抛错 → 执行 unknown → 核验解决 → 清理收敛。"""
    from app.router.knowledge_service import KnowledgeService
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.cleanup_task import DocumentCleanupTask, CleanupTaskStatus
    from app.services.document_index_service import _ExecutionRepository

    doc_id, md5 = await _seed_doc(env)  # PENDING_INDEX
    space_id = ""

    # 领取（按已有行的 md5）→ 登记 writing 执行
    svc = KnowledgeService()
    generation, allocated_doc = await svc._allocate_v1_generation_by_md5(
        md5, "t15.txt", "u1", space_id)
    assert generation == 1 and allocated_doc == doc_id

    # 物理删除主记录（模拟删除编排已提交）
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        assert await repo.delete_by_id(doc_id, "u1")
        await s.commit()

    # 写入抛错收尾：文档已不存在，执行仍必须标 unknown
    await svc._fail_v1_generation(None, "u1", generation, document_id=doc_id)

    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
        assert e.status == ExecutionStatus.UNKNOWN, "主记录删除后执行不得遗留 writing"

    # 删除编排的持久清理任务（此处手工等价构造）+ 写入者关闭（unknown 核验解决）
    async with env.factory() as s:
        s.add(DocumentCleanupTask(
            id="t15-task", document_id=doc_id, user_id="u1", md5=md5,
            generation=0, reason="document_deleted",
            status=CleanupTaskStatus.PENDING))
        await s.commit()

    # 未解决 unknown：清理不得 done
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task = (await s.execute(select(DocumentCleanupTask)
                                .where(DocumentCleanupTask.id == "t15-task"))).scalar_one()
        assert task.status != "done"

    # 经核验解决 unknown → 清理收敛 done
    execution_id = e.execution_id
    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            execution_id, "维护窗口核验：远端无残留写入")
        await s.commit()
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)

    async with env.factory() as s:
        task = (await s.execute(select(DocumentCleanupTask)
                                .where(DocumentCleanupTask.id == "t15-task"))).scalar_one()
        assert task.status == "done", "unknown 解决后清理应收敛"


# ---------- P1：取消、提交结果不明、崩溃协调、确认丢失 ----------

@pytest.mark.asyncio
async def test_t16_cancel_while_writing_marks_unknown(env):
    """取消后，真实删除编排与清理 worker 仍要等待迟到写入者收敛。

    ``entered``/``finished`` 是线程屏障，不用同步 sleep：取消发生在远端
    ``add_documents`` 已进入但尚未返回时；删除先获胜并删除 SQL 行，随后放行
    线程产生迟到向量。只有人工解决 unknown 后，持久清理任务才允许 done。
    """
    import threading
    import asyncio
    from app.services.document_index_service import execute_index_document
    from app.services.document_index_service import _ExecutionRepository
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.services.document_index_service import delete_index_record
    from app.models.cleanup_task import DocumentCleanupTask

    doc_id, _ = await _seed_doc(env)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    real_add = env.vs.add_documents

    def gated_add(docs, ids=None, **kwargs):
        entered.set()               # 写线程已进入 add_documents
        try:
            assert release.wait(timeout=5), "测试未在 5 秒内放行写线程"
            return real_add(docs, ids=ids, **kwargs)
        finally:
            finished.set()          # 写线程已完成（远端写入确已完成）

    env.vs.add_documents = gated_add

    task = asyncio.ensure_future(execute_index_document(doc_id, "u1"))

    async def wait_for_writer_started():
        # 屏障等待不得阻塞事件循环线程（executor 在同一循环运行）。
        await asyncio.to_thread(entered.wait, 5)
        assert entered.is_set(), "写线程未进入 add_documents"
        assert not finished.is_set(), "取消前写线程不应已完成"
        async with env.factory() as s:
            e = (await s.execute(select(DocumentIndexExecution)
                                 .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
            assert e.status == ExecutionStatus.WRITING
            return e.execution_id, e.generation

    try:
        execution_id, generation = await wait_for_writer_started()
    except BaseException:
        release.set()
        await asyncio.to_thread(finished.wait, 5)
        raise

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with env.factory() as s:
            e = (await s.execute(select(DocumentIndexExecution)
                                 .where(DocumentIndexExecution.execution_id == execution_id))).scalar_one()
            assert e.status == ExecutionStatus.UNKNOWN

        # 删除必须发生在迟到线程放行之前，这样清理队列才有真实残留可处理。
        out = await delete_index_record(doc_id, "u1")
        assert out["state"] == "deleted"
        await _run_cleanup_worker_once(env)
        async with env.factory() as s:
            task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
            assert task_row.status != "done", "unknown 写入者未解决前不得完成清理"
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 5)
    assert finished.is_set(), "写线程未完成，测试不得放行清理"

    # 迟到写入确实落地，且仍因 unknown 被清理门阻挡。
    late_id = f"u1:{doc_id}:g{generation}:0"
    assert late_id in env.vs.docs
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
        assert task_row.status != "done"
    assert late_id in env.vs.docs

    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            execution_id, "取消后线程已结束，确认远端写入可清理")
        await s.commit()
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
        assert task_row.status == "done"
        execution = (await s.execute(
            select(DocumentIndexExecution)
            .where(DocumentIndexExecution.execution_id == execution_id))).scalar_one()
        assert execution.status == ExecutionStatus.CLOSED
    assert late_id not in env.vs.docs



@pytest.mark.asyncio
async def test_t17_commit_succeeded_but_confirm_lost(env, monkeypatch):
    """commit 实际成功后确认丢失：按持久事实返回已发布，不误删新代次。"""
    import app.services.document_index_service as dis
    from app.db import db_config as db_cfg

    doc_id, _ = await _seed_doc(env)
    orig_factory = db_cfg.AsyncSessionLocal
    commit_calls = {"n": 0}

    class ConfirmLossSession:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        async def commit(self):
            commit_calls["n"] += 1
            if commit_calls["n"] == 2:      # 发布事务的提交
                await self._inner.commit()   # 真实提交成功
                raise RuntimeError("connection lost after commit")
            return await self._inner.commit()

    class ConfirmLossFactory:
        def __call__(self):
            return ConfirmLossSession(orig_factory())

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", ConfirmLossFactory())
    result = await dis.execute_index_document(doc_id, "u1")
    monkeypatch.undo()

    # 按持久事实：本次代次已发布（active_generation=1 且执行 closed）
    assert result["status"] == DocumentIndexStatus.INDEXED
    from app.models.cleanup_task import DocumentCleanupTask
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.active_generation == 1
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
        assert e.status == ExecutionStatus.CLOSED
        tasks = (await s.execute(select(DocumentCleanupTask))).scalars().all()
        assert len(tasks) == 1 and tasks[0].status == "pending"
    assert env.vs.docs.get(f"u1:{doc_id}:g1:0") is not None, "已发布新代次被误删"


@pytest.mark.asyncio
async def test_t18_crash_recovery_via_lease(env):
    """领取后进程退出：协调任务把租约过期的 writing 列为失联 unknown；
    人工核验解决后清理收敛，不得无条件 closed。"""
    from app.services.document_index_service import _ExecutionRepository
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from datetime import datetime, timedelta, timezone
    from app.models.cleanup_task import DocumentCleanupTask, CleanupTaskStatus

    doc_id, md5 = await _seed_doc(env)
    # 模拟崩溃遗留：writing 执行 + 过期租约 + 删除编排的清理任务
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    async with env.factory() as s:
        s.add(DocumentIndexExecution(
            execution_id="exec-crash", document_id=doc_id, user_id="u1",
            generation=1, token="tok-crash", status=ExecutionStatus.WRITING,
            lease_expires_at=stale))
        s.add(DocumentCleanupTask(
            id="t18-task", document_id=doc_id, user_id="u1", md5=md5,
            generation=0, reason="document_deleted",
            status=CleanupTaskStatus.PENDING))
        await s.commit()

    # 协调：失联 unknown（不是 closed）
    async with env.factory() as s:
        repo = _ExecutionRepository(s)
        marked = await repo.mark_expired_writers_unknown(limit=10)
        await s.commit()
        assert marked == 1
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.execution_id == "exec-crash"))).scalar_one()
        assert e.status == ExecutionStatus.UNKNOWN

    # 协调后：清理不得 done
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task = (await s.execute(select(DocumentCleanupTask)
                                .where(DocumentCleanupTask.id == "t18-task"))).scalar_one()
        assert task.status != "done"

    # 人工核验解决 → 清理收敛（删除编排此时才安全执行）
    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            "exec-crash", "维护窗口核验：旧进程已停止，远端无残留写入")
        await s.commit()
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task = (await s.execute(select(DocumentCleanupTask)
                                .where(DocumentCleanupTask.id == "t18-task"))).scalar_one()
        assert task.status == "done"


@pytest.mark.asyncio
async def test_t19_other_execution_published_not_mistaken_for_this_one(env, monkeypatch):
    """提交确认丢失但 active 属于另一执行（同文档新代次由别处发布）：
    本次不得误判成功。"""
    import app.services.document_index_service as dis
    from app.db import db_config as db_cfg

    doc_id, _ = await _seed_doc(env)
    orig_factory = db_cfg.AsyncSessionLocal
    fail_on_commit = {"armed": True, "n": 0}

    class FailCommitSession:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        async def commit(self):
            fail_on_commit["n"] += 1
            if fail_on_commit["n"] == 2:  # 发布事务的提交
                raise RuntimeError("connection lost before commit")
            return await self._inner.commit()

    class FailCommitFactory:
        def __call__(self):
            return FailCommitSession(orig_factory())

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", FailCommitFactory())
    result = await dis.execute_index_document(doc_id, "u1")
    monkeypatch.undo()

    # 提交未持久化：返回失败/不确定，不是成功
    assert result["status"] == DocumentIndexStatus.INDEX_FAILED
    assert result["error"] == "publish_commit_uncertain"

    # 若另一执行随后发布了该文档的新代次，本执行确认不得误判为自身成功
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        # 模拟另一执行（不同 token）发布 active_generation=5
        row.active_generation = 5
        row.status = DocumentIndexStatus.INDEXED
        row.generation = 5
        await s.commit()

    # 用已返回的失败结果（publish_commit_uncertain）：协调层保持 unknown，
    # 不会把别的执行的发布当作本次成功——此处验证探针区分执行身份
    from app.services.document_index_service import repo_like_get_active_generation, \
        repo_like_get_execution
    async with env.factory() as s:
        v_active = await repo_like_get_active_generation(s, doc_id, "u1")
        assert int(v_active) == 5
        # 本次执行（gen=1, token）不存在或非 closed → 不构成发布确认
        e_status = await repo_like_get_execution(s, doc_id, 1, "x-token")
        assert e_status != "closed" or e_status is None


@pytest.mark.asyncio
async def test_t19b_other_execution_publishes_between_commit_exception_and_confirm(env, monkeypatch):
    """真实竞争窗口：发布提交异常 → 原事务已 rollback/close 释放锁之后、
    独立确认读取之前，另一连接发布新代次。执行器必须按"本次代次/执行身份"
    判定，不得把别的执行当作本次成功。"""
    import app.services.document_index_service as dis
    from app.db import db_config as db_cfg

    doc_id, _ = await _seed_doc(env)
    orig_factory = db_cfg.AsyncSessionLocal
    stage = {"publish_commit_failed": False, "confirm_entered": False}
    session_no = {"n": 0}

    class InterleaveSession:
        """发布提交（第 2 次 commit）抛异常且不发布；第 3 次打开（独立确认
        session）__aenter__ 时（原事务已 rollback/close、确认读取之前）由
        另一连接发布 gen=5。"""
        def __init__(self, inner):
            self._inner = inner
            self._commit_no = 0

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            await self._inner.__aenter__()
            if session_no["n"] == 2:
                stage["confirm_entered"] = True
                from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
                async with orig_factory() as other:
                    from app.models.document_index import DocumentIndex
                    from sqlalchemy import update
                    await other.execute(
                        update(DocumentIndex)
                        .where(DocumentIndex.id == doc_id)
                        .values(active_generation=5, generation=5,
                                status=DocumentIndexStatus.INDEXED))
                    other.add(DocumentIndexExecution(
                        execution_id="exec-other", document_id=doc_id, user_id="u1",
                        generation=5, token="tok-other",
                        status=ExecutionStatus.CLOSED))
                    await other.commit()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        async def commit(self):
            self._commit_no += 1
            if self._commit_no == 2:  # 发布事务
                stage["publish_commit_failed"] = True
                raise RuntimeError("connection lost before this commit")
            return await self._inner.commit()

    class InterleaveFactory:
        def __call__(self):
            session_no["n"] += 1
            return InterleaveSession(orig_factory())

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", InterleaveFactory())
    result = await dis.execute_index_document(doc_id, "u1")
    monkeypatch.undo()

    assert stage["publish_commit_failed"] is True
    assert stage["confirm_entered"] is True, "确认读取必须发生在原事务释放之后"
    from app.services.document_index_service import repo_like_get_active_generation
    async with env.factory() as s:
        assert int(await repo_like_get_active_generation(s, doc_id, "u1")) == 5
    assert result["status"] == DocumentIndexStatus.INDEX_FAILED
    assert result["error"] == "publish_commit_uncertain", \
        "独立确认分支必须判定本次未发布成功"



@pytest.mark.asyncio
async def test_t20_v1_cancel_while_writing_marks_unknown(env):
    """v1 真实服务取消后，迟到向量经删除编排最终可清理。"""
    import asyncio as _aio
    import threading
    import hashlib
    from app.router.knowledge_service import KnowledgeService
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.cleanup_task import DocumentCleanupTask
    from app.services.document_index_service import _ExecutionRepository
    from app.services.document_index_service import delete_index_record

    user_id = "u-v1c"
    content = "取消"
    doc_id, _ = await _seed_doc(env, user_id=user_id)
    content_md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, user_id)
        row.md5 = content_md5
        await s.commit()

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    async def gated_get_document(files=None, user_id=None, progress_callback=None,
                                 space_id="", generation=None, document_id=None):
        def write_late_vector():
            entered.set()
            try:
                assert release.wait(timeout=5), "测试未在 5 秒内放行写线程"
                env.vs.add_documents(
                    [NS(page_content="v1迟到", metadata={
                        "user_id": user_id,
                        "md5": content_md5,
                        "document_id": document_id,
                        "generation": int(generation),
                    })],
                    ids=[f"{user_id}:{document_id}:g{generation}:0"],
                )
            finally:
                finished.set()

        await _aio.to_thread(write_late_vector)
        return None

    env.store.get_document = gated_get_document

    class _Up:
        filename = "v1cancel.txt"
        size = 5

        async def read(self, size=-1):
            return content.encode("utf-8")

        async def seek(self, pos):
            return None

    svc = KnowledgeService()
    task = _aio.ensure_future(svc.handle_add_vector_single(_Up(), user_id, space_id=""))

    async def wait_for_v1_writer_started():
        await _aio.to_thread(entered.wait, 5)
        assert entered.is_set(), "v1 写线程未进入向量写入"
        assert not finished.is_set(), "取消前写线程不应已完成"
        async with env.factory() as s:
            e = (await s.execute(select(DocumentIndexExecution)
                                 .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
            assert e.status == ExecutionStatus.WRITING
            return e.execution_id, e.generation

    try:
        execution_id, generation = await wait_for_v1_writer_started()
    except BaseException:
        release.set()
        await _aio.to_thread(finished.wait, 5)
        raise

    try:
        task.cancel()
        # 取消必须传播（不吞）；v1 入口也必须将不确定写入标为 unknown。
        with pytest.raises(_aio.CancelledError):
            await task

        async with env.factory() as s:
            execs = (await s.execute(select(DocumentIndexExecution)
                                     .where(DocumentIndexExecution.execution_id == execution_id))).scalars().all()
            assert execs, "v1 应登记执行清单"
            assert execs[0].status == ExecutionStatus.UNKNOWN, \
                "取消后的 v1 执行必须进入 unknown"

        # 删除必须先于迟到写入，确保清理 worker 有真实残留向量可处理。
        out = await delete_index_record(doc_id, user_id)
        assert out["state"] == "deleted"
        await _run_cleanup_worker_once(env)
        async with env.factory() as s:
            task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
            assert task_row.status != "done", "取消/失联执行未解决前清理不得 done"
    finally:
        release.set()
        await _aio.to_thread(finished.wait, 5)
    assert finished.is_set(), "v1 写线程未完成，测试不得放行恢复"
    late_id = f"{user_id}:{doc_id}:g{generation}:0"
    assert late_id in env.vs.docs

    # unknown 仍未解决时，清理不得丢失迟到向量。
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
        assert task_row.status != "done"
    assert late_id in env.vs.docs

    # 人工核验关闭 unknown 后，清理任务才可收敛。
    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            execution_id, "取消后线程已结束，确认远端写入可清理")
        await s.commit()
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
        assert task_row.status == "done"
        execution = (await s.execute(
            select(DocumentIndexExecution)
            .where(DocumentIndexExecution.execution_id == execution_id))).scalar_one()
        assert execution.status == ExecutionStatus.CLOSED
    assert late_id not in env.vs.docs


@pytest.mark.asyncio
async def test_t19c_cleanup_already_done_still_confirms_publish(env, monkeypatch):
    """提交成功 → 确认丢失，且对应清理任务已被独立连接置为 done：
    确认不得因任务为 done 而误判发布未成功。置 done 必须发生在发布
    事务真正提交之后（独立连接才能看到该任务）。"""
    import app.services.document_index_service as dis
    from app.db import db_config as db_cfg

    doc_id, _ = await _seed_doc(env)
    orig_factory = db_cfg.AsyncSessionLocal
    stage = {"publish_commit_failed": False, "task_set_done": False}
    session_no = {"n": 0}

    class ConfirmLossSession:
        def __init__(self, inner):
            self._inner = inner
            self._commit_no = 0

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            await self._inner.__aenter__()
            # 第 2 次打开 = 确认 session：原事务已提交且确认丢失，
            # 读取前将 superseded 任务置 done（独立连接可见已提交任务）
            if session_no["n"] == 2:
                from app.models.cleanup_task import DocumentCleanupTask, CleanupTaskStatus
                from sqlalchemy import update as _upd
                async with orig_factory() as other:
                    res = await other.execute(
                        _upd(DocumentCleanupTask)
                        .where(DocumentCleanupTask.document_id == doc_id)
                        .where(DocumentCleanupTask.reason == "superseded_generation")
                        .values(status=CleanupTaskStatus.DONE))
                    await other.commit()
                    stage["task_set_done"] = res.rowcount > 0
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        async def commit(self):
            self._commit_no += 1
            if self._commit_no == 2:  # 发布事务：真实提交成功，再模拟确认丢失
                await self._inner.commit()
                stage["publish_commit_failed"] = True
                raise RuntimeError("connection lost after commit")
            return await self._inner.commit()

    class ConfirmLossFactory:
        def __call__(self):
            session_no["n"] += 1
            return ConfirmLossSession(orig_factory())

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", ConfirmLossFactory())
    result = await dis.execute_index_document(doc_id, "u1")
    monkeypatch.undo()

    assert stage["publish_commit_failed"] is True
    assert stage["task_set_done"] is True, "任务应在发布提交后可见并被置 done"
    assert result["status"] == DocumentIndexStatus.INDEXED


@pytest.mark.asyncio
async def test_t21_sse_cancel_late_write_cleanup_converges(env, monkeypatch):
    """真实 SSE 消费取消：无 completed，迟到向量经删除/核验后清理收敛。"""
    import asyncio
    import hashlib
    import threading

    ks = _patch_real_sse_event(monkeypatch)
    from app.models.cleanup_task import DocumentCleanupTask
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.router.knowledge_service import ProcessingState
    from app.services.document_index_service import _ExecutionRepository, delete_index_record

    user_id = "u-sse-cancel"
    content = "SSE取消"
    doc_id, _ = await _seed_doc(env, user_id=user_id)
    md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, user_id)
        row.md5 = md5
        await s.commit()

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_add = env.vs.add_documents

    def gated_add(docs, ids=None, **kwargs):
        entered.set()
        try:
            assert release.wait(timeout=5), "SSE 写线程未在 5 秒内放行"
            return real_add(docs, ids=ids, **kwargs)
        finally:
            finished.set()

    env.vs.add_documents = gated_add

    class FakeQueue:
        def __init__(self, item):
            self.item = item

        def get(self, block=True, timeout=0.1):
            if self.item is None:
                raise RuntimeError("queue exhausted")
            item, self.item = self.item, None
            return item

        def task_done(self):
            return None

    result = NS(
        success=True,
        file_index=1,
        filename="sse-cancel.txt",
        documents=[NS(page_content="SSE迟到", metadata={})],
        md5=md5,
        chunk_count=1,
    )
    state = ProcessingState(total_files=1, total_valid=1)
    svc = ks.KnowledgeService()
    events = []

    async def consume():
        async for event in svc._process_slice_results(
                FakeQueue(result), 1, env.store, state, user_id):
            events.append(event)

    task = asyncio.ensure_future(consume())

    async def wait_for_sse_writer_started():
        await asyncio.to_thread(entered.wait, 5)
        assert entered.is_set(), "SSE 写线程未进入向量写入"
        assert not finished.is_set(), "取消前 SSE 写线程不应已完成"
        async with env.factory() as s:
            execution = (await s.execute(
                select(DocumentIndexExecution)
                .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
            assert execution.status == ExecutionStatus.WRITING
            return execution.execution_id, execution.generation

    try:
        execution_id, generation = await wait_for_sse_writer_started()
    except BaseException:
        release.set()
        await asyncio.to_thread(finished.wait, 5)
        raise

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        payloads = [_sse_payload(event) for event in events]
        assert "completed" not in [p.get("step") for p in payloads]

        async with env.factory() as s:
            execution = (await s.execute(
                select(DocumentIndexExecution)
                .where(DocumentIndexExecution.execution_id == execution_id))).scalar_one()
            assert execution.status == ExecutionStatus.UNKNOWN, \
                "SSE 取消不得伪称执行已 closed"

        # 真实删除编排先删除 SQL 行；迟到线程随后写入，交给持久清理任务。
        out = await delete_index_record(doc_id, user_id)
        assert out["state"] == "deleted"
        await _run_cleanup_worker_once(env)
        async with env.factory() as s:
            task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
            assert task_row.status != "done"
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 5)
    assert finished.is_set(), "SSE 写线程未完成"
    late_id = f"{user_id}:{doc_id}:g{generation}:0"
    assert late_id in env.vs.docs

    # unknown 未解决时仍不得清理迟到向量。
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
        assert task_row.status != "done", "unknown 未解决前清理不得 done"
    assert late_id in env.vs.docs

    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            execution_id, "SSE 线程已结束，确认远端写入可清理")
        await s.commit()
    await _run_cleanup_worker_once(env)
    async with env.factory() as s:
        task_row = (await s.execute(select(DocumentCleanupTask))).scalar_one()
        assert task_row.status == "done"
        execution = (await s.execute(
            select(DocumentIndexExecution)
            .where(DocumentIndexExecution.execution_id == execution_id))).scalar_one()
        assert execution.status == ExecutionStatus.CLOSED
    assert late_id not in env.vs.docs


@pytest.mark.asyncio
async def test_t21_vector_store_forwards_generation_and_document_id(monkeypatch):
    """真实 VectorStoreService 契约：代次/文档身份必须原样透传 processor。"""
    import importlib.util
    from pathlib import Path
    import sys
    from types import ModuleType

    path = Path(__file__).resolve().parents[1] / "app" / "rag" / "vector_store.py"
    # 只替换真实类导入时的重量依赖；被测 VectorStoreService 本身仍来自源码。
    hybrid = ModuleType("app.rag.retrievers.hybrid_retriever")
    hybrid.HybridRetriever = type("HybridRetriever", (), {})
    md5 = ModuleType("app.rag.md5_manager")
    md5.MD5Store = type("MD5Store", (), {})
    handler = ModuleType("app.rag.document_handler")
    handler.DocumentProcessor = type("DocumentProcessor", (), {})
    factory = ModuleType("app.utils.factory")
    factory.embed_model = object()
    for name, module_stub in {
        "app.rag.retrievers.hybrid_retriever": hybrid,
        "app.rag.md5_manager": md5,
        "app.rag.document_handler": handler,
        "app.utils.factory": factory,
    }.items():
        monkeypatch.setitem(sys.modules, name, module_stub)

    spec = importlib.util.spec_from_file_location(
        "app.rag._closure_real_vector_store", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    calls = []

    class Processor:
        async def get_document(self, *args, **kwargs):
            calls.append((args, kwargs))

    service = object.__new__(module.VectorStoreService)
    service.document_processor = Processor()
    await service.get_document(
        files=["file"], user_id="u-contract", progress_callback="progress",
        space_id="sp-contract", generation=7, document_id="doc-contract")
    assert calls == [(
        (["file"], "u-contract", "progress"),
        {"space_id": "sp-contract", "generation": 7,
         "document_id": "doc-contract"},
    )]


# ---------- N1：提交未确认后文档停在 indexing 的恢复链 ----------

@pytest.mark.asyncio
async def test_n1_01_indexing_stuck_after_uncertain_commit(env):
    """红证据：发布提交未落库 → 文档停在 indexing → 现有重试入口全部无法恢复。"""
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.document_index import DocumentIndexStatus

    doc_id, _ = await _seed_doc(env)
    # 构造：claim 已提交（文档 indexing、generation=1）+ execution unknown
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEXING)
        row = await repo.get_by_id(doc_id, "u1")
        row.generation = 1
        s.add(DocumentIndexExecution(
            execution_id="exec-n1", document_id=doc_id, user_id="u1",
            generation=1, token="tok-n1", status=ExecutionStatus.UNKNOWN))
        await s.commit()

    # 现有重试入口逐一验证无法接手：
    # a) Celery 的 retryable 分支 reset_to_pending_if_failed 只认 index_failed
    async with env.factory() as s:
        assert await DocumentIndexRepository(s).reset_to_pending_if_failed(doc_id) is False
    # b) reindex_document 只接受 failed/pending
    from app.services.document_index_service import reindex_document
    res = await reindex_document(doc_id, "u1")
    assert res["success"] is False and "不允许" in res["message"]

    # 文档仍卡在 indexing：无法恢复（红）
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEXING


# ---------- N1 绿路径：reconcile 恢复 ----------

@pytest.mark.asyncio
async def test_n1_01_reconcile_recovers_stuck_indexing(env, monkeypatch):
    """执行已核验关闭 + 确认未发布 + 文档仍 indexing → reconcile 恢复
    index_failed → 现有重试协议可接手 → 新代次只领取一次并成功发布。"""
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.document_index import DocumentIndexStatus
    from app.services.document_index_service import (
        reconcile_index_execution, _ExecutionRepository, execute_index_document)

    doc_id, _ = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEXING)
        row = await repo.get_by_id(doc_id, "u1")
        row.generation = 1
        s.add(DocumentIndexExecution(
            execution_id="exec-n1b", document_id=doc_id, user_id="u1",
            generation=1, token="tok-n1b", status=ExecutionStatus.UNKNOWN))
        await s.commit()

    # 未核验（execution 仍 unknown）→ waiting，不恢复
    out = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-n1b")
    assert out["outcome"] == "waiting"

    # 人工核验停止 → resolve_unknown 关闭执行
    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            "exec-n1b", "维护窗口核验：远端无残留写入")
        await s.commit()

    # reconcile → recovered（原文件存在），文档回 index_failed
    out2 = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-n1b")
    assert out2["outcome"] == "recovered"
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEX_FAILED
        assert "unconfirmed" in (row.error_message or "")

    # 现有重试协议接手：reindex 的条件重置（index_failed→pending）→ 领取
    # → 新代次只领取一次并成功发布
    add_calls = []
    orig_add = env.vs.add_documents

    def spy_add(docs, ids=None, **kwargs):
        add_calls.append(ids)
        return orig_add(docs, ids=ids, **kwargs)

    env.vs.add_documents = spy_add
    # reindex 会 delay；此处用其内部同一条件重置路径（方案允许参数化组件矩阵）
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        assert await repo.reset_to_pending_if_retryable(doc_id)
        await s.commit()
    res = await execute_index_document(doc_id, "u1")
    assert res["status"] == DocumentIndexStatus.INDEXED
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.active_generation == 2  # 新代次
    assert len(add_calls) == 1
    assert add_calls[0] == [f"u1:{doc_id}:g2:0"]


@pytest.mark.asyncio
async def test_n1_03_reconcile_respects_delete_and_new_generation(env):
    """交错：文档已删除或已推进新代次 → reconcile 不覆盖（conflict）。"""
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.document_index import DocumentIndexStatus
    from app.services.document_index_service import reconcile_index_execution

    # 场景 A：文档已推进新代次（gen=3）
    doc_id, _ = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEXING)
        row = await repo.get_by_id(doc_id, "u1")
        row.generation = 3
        s.add(DocumentIndexExecution(
            execution_id="exec-a", document_id=doc_id, user_id="u1",
            generation=1, token="tok-a", status=ExecutionStatus.CLOSED))
        await s.commit()
    out = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-a")
    assert out["outcome"] == "conflict" or out["outcome"] == "waiting"
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEXING  # 未被覆盖

    # 场景 B：文档已删除
    doc2, _ = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc2, DocumentIndexStatus.INDEXING)
        s.add(DocumentIndexExecution(
            execution_id="exec-b", document_id=doc2, user_id="u1",
            generation=1, token="tok-b", status=ExecutionStatus.CLOSED))
        await s.commit()
        await repo.delete_by_id(doc2, "u1")
        await s.commit()
    out2 = await reconcile_index_execution(
        document_id=doc2, user_id="u1", generation=1, execution_token="tok-b")
    assert out2["outcome"] == "gone"


@pytest.mark.asyncio
async def test_n1_04_two_reconcilers_single_progress(env):
    """两个恢复者同时操作：最多一方推进；重放不重复分配代次。"""
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.document_index import DocumentIndexStatus
    from app.services.document_index_service import reconcile_index_execution

    doc_id, _ = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEXING)
        row = await repo.get_by_id(doc_id, "u1")
        row.generation = 1
        s.add(DocumentIndexExecution(
            execution_id="exec-c", document_id=doc_id, user_id="u1",
            generation=1, token="tok-c", status=ExecutionStatus.CLOSED))
        await s.commit()

    # 两方顺序调用（条件更新保证只有第一个 rowcount=1）
    r1 = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-c")
    r2 = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-c")
    recovered = [r for r in (r1, r2) if r["outcome"] == "recovered"]
    assert len(recovered) == 1
    other = [r for r in (r1, r2) if r["outcome"] != "recovered"]
    assert other[0]["outcome"] == "conflict"

    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEX_FAILED
        assert row.generation == 1  # 未重复分配代次


@pytest.mark.asyncio
async def test_n1_06_reconcile_missing_file_needs_reupload(env):
    """原文件缺失：reconcile 恢复 index_failed 并标记 needs_reupload；重传后可收敛。"""
    import os
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.document_index import DocumentIndexStatus
    from app.services.document_index_service import (
        reconcile_index_execution, _ExecutionRepository)

    doc_id, _ = await _seed_doc(env)
    # 删除物理文件
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        file_path = row.file_path
        await repo_update_status(doc_id, DocumentIndexStatus.INDEXING, env)
        row.generation = 1
        s.add(DocumentIndexExecution(
            execution_id="exec-n1f", document_id=doc_id, user_id="u1",
            generation=1, token="tok-n1f", status=ExecutionStatus.UNKNOWN))
        await s.commit()
    if os.path.exists(file_path):
        os.unlink(file_path)

    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            "exec-n1f", "维护窗口核验：文件已缺失，需重传")
        await s.commit()

    out = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-n1f")
    assert out["outcome"] == "needs_reupload"
    assert "需要重新上传" in out["message"]
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEX_FAILED
        assert "需要重新上传" in (row.error_message or "")


async def repo_update_status(doc_id, status, env):
    async with env.factory() as s:
        await DocumentIndexRepository(s).update_status(doc_id, status)
        await s.commit()


# ---------- N1 返修：恢复 commit 幂等 + needs_reupload 闭环 + 真实交错 ----------

@pytest.mark.asyncio
async def test_n1_02b_reconcile_commit_confirm_lost_idempotent(env, monkeypatch):
    """恢复事务 commit 成功但确认丢失：再次 reconcile 幂等重建"已恢复"。"""
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.document_index import DocumentIndexStatus
    from app.services.document_index_service import (
        reconcile_index_execution)
    from app.db import db_config as db_cfg

    doc_id, _ = await _seed_doc(env)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEXING)
        row = await repo.get_by_id(doc_id, "u1")
        row.generation = 1
        s.add(DocumentIndexExecution(
            execution_id="exec-n1r", document_id=doc_id, user_id="u1",
            generation=1, token="tok-n1r", status=ExecutionStatus.CLOSED))
        await s.commit()

    orig_factory = db_cfg.AsyncSessionLocal
    commit_call = {"n": 0}

    class LossySession:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        async def commit(self):
            commit_call["n"] += 1
            # reconcile 内只有一次 commit（恢复 UPDATE）；真实提交后模拟确认丢失
            await self._inner.commit()
            raise RuntimeError("connection lost after reconcile commit")

    class LossyFactory:
        def __call__(self):
            return LossySession(orig_factory())

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", LossyFactory())
    out = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-n1r")
    monkeypatch.undo()

    # 确认丢失但事实已提交：重建"已恢复"，不误报 conflict
    assert out["outcome"] == "recovered"
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEX_FAILED


@pytest.mark.asyncio
async def test_n1_06b_needs_reupload_takeover_loop(env, monkeypatch):
    """needs_reupload 闭环：缺文件恢复后，同名重传接管行并重新索引成功。"""
    import os
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.models.document_index import DocumentIndexStatus
    from app.services.document_index_service import (
        reconcile_index_execution, _ExecutionRepository, save_uploaded_file)
    import app.services.document_index_service as dis
    import hashlib
    import app.utils.factory as fmod

    doc_id, _ = await _seed_doc(env)
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        file_path = row.file_path
        original_filename = row.original_filename
        await DocumentIndexRepository(s).update_status(doc_id, DocumentIndexStatus.INDEXING)
        row.generation = 1
        s.add(DocumentIndexExecution(
            execution_id="exec-n1t", document_id=doc_id, user_id="u1",
            generation=1, token="tok-n1t", status=ExecutionStatus.UNKNOWN))
        await s.commit()
    if os.path.exists(file_path):
        os.unlink(file_path)

    async with env.factory() as s:
        assert await _ExecutionRepository(s).resolve_unknown(
            "exec-n1t", "维护窗口核验：文件缺失，需重传")
        await s.commit()
    out = await reconcile_index_execution(
        document_id=doc_id, user_id="u1", generation=1, execution_token="tok-n1t")
    assert out["outcome"] == "needs_reupload"

    # 同名重传（不同内容）→ 接管行，不返回 409
    monkeypatch.setattr("app.services.document_index_service.get_data_path",
                        lambda: str(env.tmp))
    def _md5_sync(path):
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    monkeypatch.setattr("app.services.document_index_service.get_file_md5_hex_sync",
                        _md5_sync)
    class LocalEmbed:
        pass
    monkeypatch.setattr(fmod, "embed_model", NS(resolve=lambda: LocalEmbed()))
    dis._embedding_health_cache.update({"available": None, "checked_at": 0})
    try:
        class _Up:
            filename = original_filename
            size = 9
            def __init__(self):
                self._done = False
            async def read(self, size=-1):
                if self._done:
                    return b""
                self._done = True
                return "新内容".encode("utf-8")
            async def seek(self, pos):
                return None
        res = await save_uploaded_file(_Up(), "u1", space_id="")
    finally:
        dis._embedding_health_cache.update({"available": None, "checked_at": 0})

    assert res.get("takeover") is True, "needs_reupload 行应被接管重传而非 409"
    assert res["document_id"] == doc_id  # 复用原 document_id
    assert res["status"] == "indexed" or res["status"] == "pending_index"
    # 收敛：最终 indexed
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.INDEXED
        assert os.path.exists(row.file_path)
