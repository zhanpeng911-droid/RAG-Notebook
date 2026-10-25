"""统一方案 B/C 回归：可见性服务 + 存量映射工具。"""
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
    yield NS(factory=factory, tmp=tmp_path)
    await engine.dispose()


async def _seed_indexed(env, user_id="u1", status=DocumentIndexStatus.INDEXED,
                        active_generation=1):
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        name = uuid.uuid4().hex[:8]
        doc = await repo.create(user_id, f"f{name}.pdf",
                                f"{name}.pdf", "/tmp/x.pdf",
                                md5=uuid.uuid4().hex, status=status)
        doc.active_generation = active_generation
        await s.commit()
        return doc.id


# ---------- B：可见性服务按 document_id 判定 ----------

@pytest.mark.asyncio
async def test_visibility_service_scoped_and_generation(env):
    from app.services.document_visibility_service import create_visibility_service

    d1 = await _seed_indexed(env, user_id="u1", active_generation=2)
    d2 = await _seed_indexed(env, user_id="u1", status=DocumentIndexStatus.DELETING)
    d3 = await _seed_indexed(env, user_id="u2")  # 他人

    vis = create_visibility_service("u1", source="default")
    active = await vis.resolve_visible_generations([d1, d2, d3, "missing"])
    assert active.get(d1) == 2
    assert d2 not in active  # deleting 不可见
    assert d3 not in active  # 他人不可见
    assert "missing" not in active

    # 过滤器：本人当前代次放行；旧代次/缺 document_id 拒绝（fail-closed）
    docs = [
        NS(page_content="ok", metadata={"user_id": "u1", "document_id": d1,
                                        "generation": 2}),
        NS(page_content="stale", metadata={"user_id": "u1", "document_id": d1,
                                           "generation": 1}),
        NS(page_content="no-owner", metadata={"document_id": d1, "generation": 2}),
        NS(page_content="legacy", metadata={"user_id": "u1", "md5": "M"}),
    ]
    kept = await vis.filter_by_identity(docs)
    # 缺 owner 一律拒绝；旧代次拒绝；legacy 无映射拒绝
    assert [d.page_content for d in kept] == ["ok"]


# ---------- C：映射 apply 幂等 / dry-run 零写 / revoke / rollback ----------

@pytest.mark.asyncio
async def test_mapping_apply_idempotent_and_dry_run(env):
    from app.repositories.legacy_mapping_repository import LegacyMappingRepository
    from app.services.legacy_backfill_tool import dry_run, apply
    from app.models.legacy_mapping import LegacyDocumentMapping

    d1 = await _seed_indexed(env, user_id="u1")
    mappings = [{"legacy_vector_id": f"legacy-{i}", "document_id": d1,
                 "compat_generation": 0} for i in range(3)]

    # dry-run 零写
    plan = await dry_run(mappings)
    assert plan["dry_run"] is True and plan["proposed"] == 3
    async with env.factory() as s:
        assert (await s.execute(select(LegacyDocumentMapping))).scalars().all() == []

    # apply 首次
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        r1 = await apply(repo, source="col-a", user_id="u1", mappings=mappings,
                         batch="B1", evidence_summary="dry-run 样本")
        await s.commit()
        assert r1["applied"] == 3
        rows = (await s.execute(select(LegacyDocumentMapping))).scalars().all()
        assert len(rows) == 3

    # 幂等重放：同一 (source, legacy_vector_id) 不重复
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        r2 = await apply(repo, source="col-a", user_id="u1", mappings=mappings,
                         batch="B1", evidence_summary="dry-run 样本")
        await s.commit()
        assert r2["applied"] == 3  # upsert 幂等
        rows = (await s.execute(select(LegacyDocumentMapping))).scalars().all()
        assert len(rows) == 3

    # active 查询
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        got = await repo.get_active_for_owner("col-a", "u1",
                                              ["legacy-0", "legacy-1", "nope"])
        assert got == {"legacy-0": d1, "legacy-1": d1}

    # revoke：新代次发布后旧映射失效
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        n = await repo.revoke("col-a", "u1", d1)
        await s.commit()
        assert n == 3
        assert await repo.get_active_for_owner("col-a", "u1", ["legacy-0"]) == {}


@pytest.mark.asyncio
async def test_mapping_rollback_limited_to_batch(env):
    from app.repositories.legacy_mapping_repository import LegacyMappingRepository
    from app.services.legacy_backfill_tool import apply, rollback
    from app.models.legacy_mapping import LegacyDocumentMapping

    d1 = await _seed_indexed(env, user_id="u1")
    m1 = [{"legacy_vector_id": "a1", "document_id": d1}]
    m2 = [{"legacy_vector_id": "a2", "document_id": d1}]
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        await apply(repo, source="col-b", user_id="u1", mappings=m1, batch="B1",
                    evidence_summary="s")
        await apply(repo, source="col-b", user_id="u1", mappings=m2, batch="B2",
                    evidence_summary="s")
        await s.commit()
    # rollback 只撤销 B1（置 REVOKED 保留审计；B2 不受影响）
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        r = await rollback(repo, source="col-b", batch="B1", owner="u1")
        assert r["revoked"] == 1
    async with env.factory() as s:
        from app.models.legacy_mapping import LegacyMappingStatus
        rows = (await s.execute(select(LegacyDocumentMapping))).scalars().all()
        assert len(rows) == 2
        by_batch = {x.source_batch: x.status for x in rows}
        assert by_batch["B1"] == LegacyMappingStatus.REVOKED.value  # 撤销保留
        assert by_batch["B2"] == LegacyMappingStatus.ACTIVE.value   # 后续批次不受影响


# ---------- 复验补齐：C 冲突保护 / legacy 读取 / A ABA ----------

@pytest.mark.asyncio
async def test_mapping_conflict_preserves_original(env):
    """归属变化必须冲突保留：不覆盖原映射，原数据保持。"""
    from app.repositories.legacy_mapping_repository import (
        LegacyMappingRepository, ConflictError)
    from app.models.legacy_mapping import LegacyDocumentMapping

    d1 = await _seed_indexed(env, user_id="u1")
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        await repo.upsert(source="col-c", legacy_vector_id="L1", owner="u1",
                          document_id=d1, compat_generation=0)
        await s.commit()
    # 归属变化（另一 owner/另一 document）→ ConflictError，原映射保留
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        try:
            await repo.upsert(source="col-c", legacy_vector_id="L1", owner="u2",
                              document_id=d1)
            assert False, "归属变化应抛 ConflictError"
        except ConflictError:
            # 编排层在同一 session 登记冲突（不隐式提交调用方其它工作）
            await s.commit()
        pass
    async with env.factory() as s:
        rows = (await s.execute(select(LegacyDocumentMapping)
                                .where(LegacyDocumentMapping.legacy_vector_id == "L1"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].owner == "u1"  # 原归属保留
        assert rows[0].status == "unresolved"  # 标记冲突待人工


@pytest.mark.asyncio
async def test_legacy_mapping_enables_read(env):
    """legacy 存量读取契约（复验修正）：
    - compat_generation 必须等于当前 active_generation 才可见；
    - compat=0 不得混入已发布新代次（active=1）——该场景不可见；
    - active=0（未发布新代次）时存量可读；发布新代次后旧映射不可见。"""
    from app.services.document_visibility_service import create_visibility_service
    from app.repositories.legacy_mapping_repository import LegacyMappingRepository

    d1 = await _seed_indexed(env, user_id="u1", active_generation=1)
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        await repo.upsert(source="default", legacy_vector_id="legacy-a", owner="u1",
                          document_id=d1, compat_generation=0)
        await s.commit()

    vis = create_visibility_service("u1", source="default")
    docs = [
        NS(page_content="存量正文", metadata={"chunk_id": "legacy-a",
                                            "user_id": "u1"}),
        NS(page_content="未解析", metadata={"chunk_id": "nope", "user_id": "u1"}),
        NS(page_content="他人", metadata={"chunk_id": "legacy-a", "user_id": "u2"}),
    ]
    kept = await vis.filter_by_identity(docs)
    # 缺 owner 拒绝；未解析拒绝；compat=0 但 active=1 → 存量不可见（修正预期）
    assert kept == []

    # active=0（未发布新代次）时存量可读
    d2 = await _seed_indexed(env, user_id="u1", active_generation=0)
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        await repo.upsert(source="default", legacy_vector_id="legacy-b", owner="u1",
                          document_id=d2, compat_generation=0)
        await s.commit()
    kept2 = await vis.filter_by_identity(
        [NS(page_content="存量正文", metadata={"chunk_id": "legacy-b",
                                              "user_id": "u1"})])
    assert [d.page_content for d in kept2] == ["存量正文"]

    # 发布新代次（active=1 + revoke）后：旧映射不可见
    async with env.factory() as s:
        repo = LegacyMappingRepository(s)
        await repo.revoke("default", "u1", d2)
        await s.commit()
    kept3 = await vis.filter_by_identity(
        [NS(page_content="存量正文", metadata={"chunk_id": "legacy-b",
                                              "user_id": "u1"})])
    assert kept3 == []


async def _seed_doc(env, user_id="u1", status=DocumentIndexStatus.PENDING_INDEX):
    path = str(env.tmp / f"{uuid.uuid4().hex}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("正文")
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        doc = await repo.create(user_id, f"f{uuid.uuid4().hex[:6]}.pdf",
                                f"{uuid.uuid4().hex[:8]}.pdf", path,
                                md5=uuid.uuid4().hex, status=status)
        await s.commit()
        return doc.id, doc.md5

@pytest.mark.asyncio
async def test_takeover_aba_rejects_stale_expected(env):
    """ABA：读取后行已完成一轮（failed→pending→indexing→failed），
    旧接管请求带过期预期代次/文件身份 → 条件不命中，拒绝。"""
    from app.repositories.document_index_repository import DocumentIndexRepository
    from app.models.document_index import DocumentIndexStatus

    doc_id, _ = await _seed_doc(env, user_id="u1", status=DocumentIndexStatus.INDEX_FAILED)
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        row = await repo.get_by_id(doc_id, "u1")
        row.generation = 1
        old_path = row.file_path
        await s.commit()

    # 模拟 ABA：另一请求先接管完成一轮（代次推进、文件路径变化、又失败）
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        row = await repo.get_by_id(doc_id, "u1")
        row.generation = 3
        row.file_path = "/new/path.txt"
        await s.commit()

    # 旧接管请求（预期 gen=1 + 旧路径）→ 不命中
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        ok = await repo.takeover_upload_row(
            doc_id=doc_id, user_id="u1",
            filename="new.pdf", file_path="/tmp/x.pdf", md5="M",
            file_size=1, file_type=".pdf",
            expected_generation=1, expected_file_path=old_path)
        assert ok is False  # ABA 拒绝
        row = await repo.get_by_id(doc_id, "u1")
        assert row.generation == 3  # 胜者状态未被覆盖
