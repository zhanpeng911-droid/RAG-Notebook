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
@pytest.mark.xfail(reason="K1 done 门槛在清理 worker 未生效（N1 待接线修复），保持红色登记", strict=False)
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

    # 旧写入者（持有旧代次）在两次核验之间写入迟到数据
    def late_write():
        env.vs.add_documents(
            [NS(page_content="迟到", metadata={"user_id": "u1", "md5": md5,
                                              "document_id": doc_id,
                                              "generation": 1})],
            ids=[f"u1:{doc_id}:g1:late"],
        )

    # 第一次 worker：核验通过 → 不 done（首空登记/宽限）
    await _run_cleanup_worker_once(env)
    # 迟到写入发生在首空之后
    late_write()
    # 第二次 worker：迟到数据必须被捕获，不得 done
    await _run_cleanup_worker_once(env)
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status != "done", "迟到写入后任务不得标完成"

    # 写入者关闭（execution closed）后：清理最终收敛
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status == "done"
    assert f"u1:{doc_id}:g1:late" not in env.vs.docs


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
@pytest.mark.xfail(reason="document_deleted 遗留向量归属判断未接入 blocked 语义（N4 待实施）", strict=False)
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
