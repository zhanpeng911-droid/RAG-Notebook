"""S03 删除编排回归 —— pending 残余清理、批删逐项结果、失败不删 SQL。

修复前缺陷：
- pending 文档（F09 重试路径可能有部分向量）删除时跳过向量清理；
- 批删中向量/MD5/文件失败仍删除 SQL 并返回 success；
- VectorStoreService 初始化失败时批删照样删 SQL；
- 文件 helper 无可判定返回值。
"""
import os
import uuid
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.document_index_service as dis
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
    monkeypatch.setattr(dis, "get_data_path", lambda: str(tmp_path))

    # 向量库假实例：记录 delete 调用，可按 md5 注入失败
    box = {"delete_calls": [], "fail_md5": set(), "fail_all": False}

    def _delete(where=None, ids=None):
        box["delete_calls"].append(where)
        if box["fail_all"]:
            raise RuntimeError("chroma down")
        if where and "$and" in where:
            for cond in where["$and"]:
                if "md5" in cond and cond["md5"] in box["fail_md5"]:
                    raise RuntimeError(f"chroma down for {cond['md5']}")

    class FakeVSCls:
        def __new__(cls):
            raise RuntimeError("VectorStoreService 初始化失败（按需打开）")

    store_impl = NS(
        vectors_store=NS(delete=_delete),
        save_md5_hex_seen=[],
    )

    async def _save_md5(m, filename=None, original=None, u=None):
        store_impl.save_md5_hex_seen.append((m, filename))

    store_impl.save_md5_hex = _save_md5

    class RealFakeVSCls:
        def __new__(cls):
            if box.get("init_fails"):
                raise RuntimeError("chroma 初始化失败")
            return store_impl

    import sys
    monkeypatch.setattr(sys.modules["app.rag.vector_store"],
                        "VectorStoreService", RealFakeVSCls)
    box["raise_cls"] = FakeVSCls
    box["real_cls"] = RealFakeVSCls
    box["store"] = store_impl

    # 记录服务桩（可注入失败）
    record_box = {"deleted": [], "fail_md5": set()}

    class FakeRec:
        async def delete_single_md5(self, user_id, md5, delete_documents=True):
            if md5 in record_box["fail_md5"]:
                raise RuntimeError("md5 记录清理失败")
            record_box["deleted"].append((user_id, md5))

    import app.services.knowledge_record_service as krs
    monkeypatch.setattr(krs, "KnowledgeRecordService", FakeRec)

    yield NS(factory=factory, tmp=tmp_path, box=box, records=record_box)
    await engine.dispose()


async def _seed_doc(env, user_id="u1", status=DocumentIndexStatus.PENDING_INDEX,
                    with_file=True):
    path = None
    if with_file:
        # 文件必须位于该用户的存储目录内，否则物理删除会被路径守卫拒绝
        user_dir = os.path.join(str(env.tmp), "knowledge_files", user_id)
        os.makedirs(user_dir, exist_ok=True)
        path = os.path.join(user_dir, f"{uuid.uuid4().hex}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("正文")
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        md5 = uuid.uuid4().hex
        doc = await repo.create(user_id, f"{md5}.pdf", f"{md5}-x.pdf",
                                path or "/missing/file.pdf",
                                md5=md5, status=status)
        await s.commit()
        return doc.id, doc.md5


async def _row(env, doc_id, user_id="u1"):
    async with env.factory() as s:
        doc = await DocumentIndexRepository(s).get_by_id(doc_id, user_id)
        return doc


# ---------- pending 残余向量清理 ----------

@pytest.mark.asyncio
async def test_delete_pending_doc_attempts_vector_cleanup(env):
    """pending（可能已有部分向量）删除必须尝试向量清理，不能凭状态推断跳过。"""
    doc_id, md5 = await _seed_doc(env, status=DocumentIndexStatus.PENDING_INDEX)
    out = await dis.delete_index_record(doc_id, "u1")
    assert out["success"] is True
    assert env.box["delete_calls"], "pending 文档删除未尝试向量清理（S03）"
    # 向量清理按 user_id + md5 定位
    assert any(md5 in str(c) for c in env.box["delete_calls"])


@pytest.mark.asyncio
async def test_delete_pending_vector_failure_keeps_record(env):
    """pending 文档向量清理失败：保留记录（deleting）可重试，不删 SQL。"""
    doc_id, md5 = await _seed_doc(env, status=DocumentIndexStatus.PENDING_INDEX)
    env.box["fail_md5"].add(md5)
    out = await dis.delete_index_record(doc_id, "u1")
    assert out["success"] is False
    assert await _row(env, doc_id) is not None


@pytest.mark.asyncio
async def test_delete_md5_cleanup_failure_keeps_record(env):
    """MD5 记录清理失败：保留可恢复依据，不得宣称删除成功。"""
    doc_id, md5 = await _seed_doc(env, status=DocumentIndexStatus.INDEXED)
    env.records["fail_md5"].add(md5)
    out = await dis.delete_index_record(doc_id, "u1")
    assert out["success"] is False
    assert await _row(env, doc_id) is not None


# ---------- 批删逐项结果 ----------

@pytest.mark.asyncio
async def test_batch_delete_partial_failure_reports_per_item(env):
    """批删两文档、一个向量失败：逐项结果、计数正确，失败项保留 SQL。"""
    ok_id, ok_md5 = await _seed_doc(env, status=DocumentIndexStatus.INDEXED)
    bad_id, bad_md5 = await _seed_doc(env, status=DocumentIndexStatus.INDEXED)
    env.box["fail_md5"].add(bad_md5)

    res = await dis.clean_user_index_records("u1")
    assert res["deleted_count"] == 1, "只有确认清理完成的条目才能计入 deleted_count"
    assert res["failed_count"] == 1
    assert len(res["results"]) == 2
    by_state = {r["document_id"]: r["state"] for r in res["results"]}
    assert by_state[ok_id] == "deleted"
    assert by_state[bad_id] in ("deleting", "failed")
    assert await _row(env, bad_id) is not None, "失败项 SQL 记录不得删除"
    assert await _row(env, ok_id) is None

    # 重试失败项：恢复后真正收敛
    env.box["fail_md5"].clear()
    res2 = await dis.clean_user_index_records("u1")
    assert res2["deleted_count"] == 1 and res2["failed_count"] == 0
    assert await _row(env, bad_id) is None


@pytest.mark.asyncio
async def test_batch_delete_vector_init_failure_deletes_nothing(env):
    """VectorStoreService 初始化失败：不得删 SQL（保留可恢复依据）。"""
    doc_id, _ = await _seed_doc(env, status=DocumentIndexStatus.INDEXED)
    env.box["init_fails"] = True
    try:
        res = await dis.clean_user_index_records("u1")
        assert res["deleted_count"] == 0
        assert res["failed_count"] == 1
        assert await _row(env, doc_id) is not None
    finally:
        env.box["init_fails"] = False


@pytest.mark.asyncio
async def test_batch_delete_isolated_users(env):
    """批删只影响目标用户（空间/用户隔离）。"""
    a_id, _ = await _seed_doc(env, user_id="u-a", status=DocumentIndexStatus.INDEXED)
    b_id, _ = await _seed_doc(env, user_id="u-b", status=DocumentIndexStatus.INDEXED)
    res = await dis.clean_user_index_records("u-a")
    assert res["deleted_count"] == 1
    assert await _row(env, a_id, "u-a") is None
    assert await _row(env, b_id, "u-b") is not None


# ---------- 文件 helper 可判定结果 ----------

def test_safe_delete_physical_file_reports_outcome(tmp_path, monkeypatch):
    """文件 helper 必须返回可判定结果，不以写日志代表成功。"""
    user_dir = dis._get_storage_dir("u-file")
    monkeypatch.setattr(dis, "get_data_path", lambda: str(tmp_path))
    user_dir = os.path.join(str(tmp_path), "knowledge_files", "u-file")
    os.makedirs(user_dir, exist_ok=True)
    victim = os.path.join(user_dir, "x.txt")
    with open(victim, "w") as f:
        f.write("data")

    assert dis._safe_delete_physical_file(victim, "u-file") is True
    assert dis._safe_delete_physical_file(victim, "u-file") is True  # 已不存在：幂等成功
    # 用户目录外：拒绝并返回 False
    outside = tmp_path / "outside.txt"
    outside.write_text("no")
    assert dis._safe_delete_physical_file(str(outside), "u-file") is False
    assert outside.exists()
