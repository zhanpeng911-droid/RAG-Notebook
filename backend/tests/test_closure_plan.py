"""收口方案 C1—C4 负向回归（修复前固化失败证据）。

- C1：v1 上传把 deleting 改回 indexing（复活）——修复前可复现；
- C2：reclaim 的 IN(SELECT...LIMIT) MySQL 1235 兼容性（源码结构断言）；
- C4：删除后迟到写入缺少持久清理依据（删除未创建清理任务）。
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

    async def _noop_get_document(files=None, user_id=None, progress_callback=None, space_id="", generation=None):
        return None

    store_impl = NS(
        get_file_document=None,
        get_document=_noop_get_document,
        split_documents_sync=lambda docs: docs,
        vectors_store=NS(add_documents=_add_documents, delete=_delete,
                         get=lambda ids=None, where=None, include=None:
                         {"ids": [], "documents": [], "metadatas": []}),
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

    import app.services.knowledge_record_service as krs

    class FakeRec:
        async def delete_single_md5(self, user_id, md5, delete_documents=True):
            box.setdefault("md5_deleted", []).append((user_id, md5))

    monkeypatch.setattr(krs, "KnowledgeRecordService", FakeRec)

    yield NS(factory=factory, tmp=tmp_path, store=store_impl, box=box)
    await engine.dispose()


async def _seed_doc(env, user_id="u1", status=DocumentIndexStatus.PENDING_INDEX):
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
        await s.commit()
        return doc.id, doc.md5


# ---------- C1：v1 上传复活 deleting 文档 ----------

@pytest.mark.asyncio
async def test_v1_upload_on_deleting_doc_is_rejected(env):
    """deleting 文档的 v1 上传必须 409 拒绝：状态/代次不变、零向量写入。"""
    from fastapi import HTTPException
    from app.router.knowledge_service import KnowledgeService

    doc_id, md5 = await _seed_doc(env, status=DocumentIndexStatus.DELETING)

    class _Up:
        filename = "v1.txt"
        size = 5

        async def read(self, size=-1):
            return "内容".encode("utf-8")

        async def seek(self, pos):
            return None

    svc = KnowledgeService()
    # 内容 md5 与已存在行不同 → 走新行创建；同内容复现需要相同 md5：
    # 直接以同 md5 场景验证（上传同内容文件命中 deleting 行）
    import hashlib
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        content_md5 = row.md5

    real_hashlib_md5 = hashlib.md5

    def fake_md5(data):
        if data == "内容".encode("utf-8"):
            # 让服务计算的 md5 命中已存在的 deleting 行
            return type("H", (), {"hexdigest": lambda self: content_md5})()
        return real_hashlib_md5(data)


    # _allocate_v1_generation 内部用 hashlib.md5(content).hexdigest()
    import hashlib as _h
    orig_hexdigest = _h.md5

    class FakeHash:
        def __init__(self, data):
            self._data = data

        def hexdigest(self):
            if self._data == "内容".encode("utf-8"):
                return content_md5
            return orig_hexdigest(self._data).hexdigest()

    _h.md5 = FakeHash  # 临时替换（测试内恢复）
    try:
        with pytest.raises(HTTPException) as ei:
            await svc.handle_add_vector_single(_Up(), "u1", space_id="")
        assert ei.value.status_code == 409
    finally:
        _h.md5 = orig_hexdigest

    # 状态/代次不变，零向量写入
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.status == DocumentIndexStatus.DELETING
    assert env.box["add_calls"] == []


# ---------- C2：回收 SQL 的 MySQL 兼容结构 ----------

def test_reclaim_two_phase_structure():
    """reclaim 必须两阶段：先物化 ID 列表，再独立 UPDATE（MySQL 8.0 不支持
    IN(SELECT...LIMIT)，错误 1235）；limit 有界。"""
    import inspect
    import re
    from app.repositories.agent_run_repository import AgentRunRepository
    src = inspect.getsource(AgentRunRepository.reclaim_stale_runs)
    # 不得把 SELECT 子查询直接嵌进 IN()
    assert not re.search(r"\.in_\(\s*select", src), "仍是 IN(SELECT...) 形态"
    # 第一阶段物化列表 + 空列表短路 + 有界 limit
    assert "stale_ids = list(" in src
    assert "if not stale_ids:" in src
    assert "bounded_limit" in src


# ---------- C4：删除未创建持久清理任务 ----------

@pytest.mark.asyncio
async def test_delete_creates_persistent_cleanup_task(env, monkeypatch):
    """删除必须登记持久清理任务（迟到写入/失败重试的依据），不能只靠 recent-50 扫描。"""
    from app.services import document_index_service as dis
    monkeypatch.setattr(dis, "get_data_path", lambda: str(env.tmp))
    from app.services.document_index_service import delete_index_record
    doc_id, _ = await _seed_doc(env, status=DocumentIndexStatus.INDEXED)

    out = await delete_index_record(doc_id, "u1")
    assert out["state"] == "deleted"

    # 期望存在清理任务记录；模型尚不存在 → 本用例在实现前失败
    from app.models.cleanup_task import DocumentCleanupTask as CleanupTask  # noqa: F401
    async with env.factory() as s:
        tasks = (await s.execute(select(CleanupTask))).scalars().all()
        assert len(tasks) == 1
        assert tasks[0].document_id == doc_id
