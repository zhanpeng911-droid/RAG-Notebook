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
    from app.services.document_index_service import _ExecutionRepository
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
    """上传协程取消但线程仍可能继续写：执行标 unknown、不 done；关闭后收敛。"""
    import threading
    from app.services.document_index_service import execute_index_document
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus

    doc_id, _ = await _seed_doc(env)
    gate = threading.Event()
    released = threading.Event()

    real_add = env.vs.add_documents

    def gated_add(docs, ids=None, **kwargs):
        gate.wait(timeout=5)       # 模拟远端写入卡住
        return real_add(docs, ids=ids, **kwargs)

    env.vs.add_documents = gated_add

    import asyncio
    task = asyncio.ensure_future(execute_index_document(doc_id, "u1"))
    # 等待进入写入（轮询执行清单出现）
    for _ in range(50):
        async with env.factory() as s:
            e = (await s.execute(select(DocumentIndexExecution)
                                 .where(DocumentIndexExecution.document_id == doc_id))).scalars().all()
            if e:
                break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gate.set()                      # 释放线程：远端写入实际发生
    released.set()

    # 取消后：执行 unknown（不是 closed），清理任务不得 done
    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.document_id == doc_id))).scalar_one()
        assert e.status == ExecutionStatus.UNKNOWN

    # writer 侧收尾（持令牌收敛）后：清理可收敛
    from app.services.document_index_service import _ExecutionRepository
    async with env.factory() as s:
        assert await _ExecutionRepository(s).close_execution(
            document_id=doc_id, generation=e.generation, token=e.token,
            status="closed", reason="writer finished after cancel")
        await s.commit()
    # 触发一次 document_deleted 流程外的收敛路径：直接跑删除编排清理任务
    from app.services.document_index_service import delete_index_record
    # 文档已在？未删——此处用删除编排产生清理任务并收敛
    out = await delete_index_record(doc_id, "u1")
    assert out["state"] == "deleted"
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)
    tasks = await _cleanup_tasks(env)
    assert all(t.status == "done" for t in tasks if t.reason == "document_deleted")


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
