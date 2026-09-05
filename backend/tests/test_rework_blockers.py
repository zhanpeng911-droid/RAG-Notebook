"""验收返修阻断项回归（评审 2026-09-05 第二次）。

- P0 #1：发布指针先提交，之后才允许回收旧代次向量（提交失败不破坏可检索性）；
- P1 #2：Agent 入口 space_id 成员资格校验（移出组织不得检索原空间）；
- P1 #3：get_document_chunk 所有权 + 文档可见性 + 代次验证；
- P1 #4：剩余预算 0–3 秒跳过评分时正常返回答案（不再 UnboundLocalError）；
- P1 #5：反馈写入加入父会话删除屏障（锁内复核 tombstone）；
- P1 #7：008 downgrade 存在已发布代次时拒绝（源码守卫）；
- P1 #8：sync_note_vector_task 执行前清理跨循环陈旧连接（源码守卫）；
- P1 #9：R6C 心跳 lease + limit 实际生效；
- P1 #6：v1 上传登记 document_index 并携带代次；读取侧 gen-less 规则收紧。
"""
import uuid
from types import SimpleNamespace as NS

import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.validator import get_settings
from app.models.chat_history import Base, ChatSession
from app.models.document_index import DocumentIndexStatus
from app.repositories.document_index_repository import DocumentIndexRepository
from app.repositories.agent_run_repository import AgentRunRepository

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"
USER_B = "u-bbbb-0000-0000-000000000002"


def _auth(user_id):
    return {"Authorization": f"Bearer {jose_jwt.encode({'user_id': user_id, 'user_name': 'u'}, SECRET, algorithm='HS256')}"}


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


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
    box = {"delete_calls": [], "add_calls": [], "get_results": {}}

    def _delete(where=None, ids=None):
        box["delete_calls"].append({"where": where, "ids": ids})

    def _add_documents(docs, **kwargs):
        box["add_calls"].append((docs, kwargs.get("ids")))

    def _get(ids=None, where=None, include=None):
        if ids is not None:
            return box["get_results"].get(tuple(ids), {"ids": [], "documents": [], "metadatas": []})
        return {"ids": [], "documents": [], "metadatas": []}

    store_impl = NS(
        get_file_document=None,
        split_documents_sync=lambda docs: docs,
        vectors_store=NS(add_documents=_add_documents, delete=_delete, get=_get),
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


# ---------- P0 #1：发布提交先于旧代次清理 ----------

@pytest.mark.asyncio
async def test_publish_commit_precedes_vector_cleanup(env, monkeypatch):
    """清理动作执行时，发布指针必须已经提交（另一连接可见 active_generation）。"""
    import app.services.document_index_service as dis

    doc_id, _ = await _seed_doc(env)
    observations = []

    orig_cleanup = dis._cleanup_superseded_generations

    async def spy_cleanup(*a, **k):
        # 清理执行瞬间，从独立连接读取发布状态
        async with env.factory() as s:
            repo = DocumentIndexRepository(s)
            row = await repo.get_by_id(doc_id, "u1")
            observations.append((row.status.value, row.active_generation))
        return await orig_cleanup(*a, **k)

    monkeypatch.setattr(dis, "_cleanup_superseded_generations", spy_cleanup)

    result = await dis.execute_index_document(doc_id, "u1")
    assert result["status"] == DocumentIndexStatus.INDEXED
    assert observations, "发布后未执行旧代次清理"
    # 清理发生时发布指针已持久化（另一连接可见），P0 顺序成立
    assert observations[0] == ("indexed", 1)


@pytest.mark.asyncio
async def test_publish_commit_failure_keeps_old_generation(env, monkeypatch):
    """发布提交失败：旧代次向量不被删除，文档保持旧代次可检索。"""
    import app.services.document_index_service as dis

    doc_id, md5 = await _seed_doc(env)

    # 先建立已发布代次 1
    r1 = await dis.execute_index_document(doc_id, "u1")
    assert r1["status"] == DocumentIndexStatus.INDEXED
    deletes_before = len(env.box["delete_calls"])

    # 模拟重跑（失败态）且提交失败
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        await repo.update_status(doc_id, DocumentIndexStatus.INDEX_FAILED)
        await repo.reset_to_pending_if_failed(doc_id)
        await s.commit()

    from app.db import db_config as db_cfg

    orig_factory = db_cfg.AsyncSessionLocal

    class FailingCommitFactory:
        def __call__(self):
            return _FailingCommitSession(orig_factory())

    class _FailingCommitSession:
        def __init__(self, inner):
            self._inner = inner
            self._commits = 0

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        async def commit(self):
            self._commits += 1
            # claim 提交成功；发布提交（第二次）失败
            if self._commits >= 2:
                raise RuntimeError("simulated commit failure")
            return await self._inner.commit()

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", FailingCommitFactory())
    try:
        await dis.execute_index_document(doc_id, "u1")
    except Exception:
        pass
    finally:
        monkeypatch.setattr(db_cfg, "AsyncSessionLocal", orig_factory)

    # 提交失败：不得删除任何向量（旧代次保持可检索）
    assert len(env.box["delete_calls"]) == deletes_before
    # 数据库仍指向旧代次
    async with env.factory() as s:
        row = await DocumentIndexRepository(s).get_by_id(doc_id, "u1")
        assert row.active_generation == 1


# ---------- P1 #2：Agent 入口空间成员资格 ----------

@pytest_asyncio.fixture
async def client(env, monkeypatch):
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.agent_router import agent_router
    from app.models.space import Space
    from app.models.organization import Organization, OrganizationMember

    app = FastAPI()
    app.include_router(agent_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with env.factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 组织 + 空间：A 是成员，B 不是
    async with env.factory() as s:
        s.add(Organization(id="org-1", name="组织", owner_id=USER_A))
        s.add(OrganizationMember(id="m-1", org_id="org-1", user_id=USER_A))
        s.add(Space(id="sp-1", org_id="org-1", name="空间", created_by=USER_A))
        s.add(ChatSession(id="sess-1", user_id=USER_A, title="会话"))
        await s.commit()

    from app.services.database_session_manager import init_database_session_manager
    await init_database_session_manager()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c._factory = env.factory
        yield c


@pytest.mark.asyncio
async def test_agent_rejects_non_member_space(client, monkeypatch):
    """非组织成员用 space_id 请求 Agent：403，provider 不被调用，不创建 run。"""
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"answer": "a", "citations": [], "error": None}

    monkeypatch.setattr("app.agentic.graph.run_agent", fake_run)

    r = await client.post("/api/v1/chat/agent/query",
                          headers=_auth(USER_B),
                          json={"query": "q", "space_id": "sp-1"})
    assert r.status_code == 403
    assert calls == []

    # 成员正常放行
    r2 = await client.post("/api/v1/chat/agent/query",
                           headers=_auth(USER_A),
                           json={"query": "q", "space_id": "sp-1"})
    assert r2.status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_agent_stream_rejects_non_member_space(client, monkeypatch):
    """流式入口同样校验空间成员资格。"""
    calls = []

    async def fake_stream(**kwargs):
        calls.append(kwargs)
        yield {"type": "completed", "answer": "a", "citations": []}

    monkeypatch.setattr("app.agentic.graph.run_agent_stream", fake_stream)

    r = await client.post("/api/v1/chat/agent/query/stream",
                          headers=_auth(USER_B),
                          json={"query": "q", "space_id": "sp-1"})
    assert r.status_code == 403
    assert calls == []


# ---------- P1 #3：切片读取所有权 + 代次 ----------

@pytest.mark.asyncio
async def test_get_document_chunk_ownership_and_generation(env, monkeypatch):
    from app.agentic.tools import get_document_chunk

    own = {"user_id": "u1", "md5": "M1", "generation": 2}
    env.box["get_results"] = {
        ("chunk-own",): {"ids": ["chunk-own"], "documents": ["正文"], "metadatas": [dict(own)]},
        ("chunk-other",): {"ids": ["chunk-other"], "documents": ["别人的"],
                           "metadatas": [{"user_id": "u2", "md5": "M1", "generation": 2}]},
        ("chunk-stale",): {"ids": ["chunk-stale"], "documents": ["旧代次"],
                           "metadatas": [{"user_id": "u1", "md5": "M1", "generation": 1}]},
        ("chunk-noid",): {"ids": ["chunk-noid"], "documents": ["无标识"],
                          "metadatas": [{"md5": "M1"}]},
    }

    async def _active(user_id, md5s):
        return {"M1": 2}

    import app.services.document_index_service as dis
    monkeypatch.setattr(dis, "get_active_document_md5s", _active)

    # 本人当前代次：放行
    out = await get_document_chunk("chunk-own", "u1")
    assert out is not None and out["content"] == "正文"

    # 他人切片：拒绝（隔离探针场景）
    assert await get_document_chunk("chunk-other", "u1") is None
    # 旧代次：拒绝
    assert await get_document_chunk("chunk-stale", "u1") is None
    # 缺 user_id 标识：拒绝（fail-closed）
    assert await get_document_chunk("chunk-noid", "u1") is None


# ---------- P1 #4：跳过评分时正常返回 ----------

@pytest.mark.asyncio
async def test_generate_with_tiny_remaining_budget_returns_answer():
    """剩余预算 0–3 秒：跳过评分但正常返回答案（此前 UnboundLocalError → generation_failed）。"""
    import time as _time
    from app.agentic.answer_generator import AnswerGenerator
    from app.rag.retrieval_service import Evidence

    class FastModel:
        async def ainvoke(self, msgs):
            return type("Resp", (), {"content": "及时答案"})()

    gen = AnswerGenerator()
    gen._chat_model = FastModel()
    ev = Evidence(source_type="knowledge", source_id="k1", chunk_id="c1",
                  title="t", content="c", score=0.9, metadata={})

    out = await gen.generate("q", [ev], deadline=_time.monotonic() + 2.5)
    assert out["answer"] == "及时答案"
    assert out["timed_out"] is False
    assert out.get("error") is None
    assert out["quality_scores"] is None  # 评分被跳过而非报错


# ---------- P1 #5：反馈屏障 ----------

@pytest.mark.asyncio
async def test_feedback_blocked_when_session_deleted_behind_race(client, monkeypatch):
    """竞态模拟：get_run 返回了 run（删除在其后提交），屏障锁内复核仍拒绝。"""
    from app.models.agent_run import AgentRun
    from datetime import datetime, timezone

    async with client._factory() as s:
        s.add(ChatSession(id="sess-fb", user_id=USER_A, title="会话"))
        s.add(AgentRun(id="run-fb", user_id=USER_A, session_id="sess-fb",
                       query="q", status="completed"))
        await s.commit()

    import app.repositories.agent_run_repository as repo_mod

    orig_get_run = repo_mod.AgentRunRepository.get_run

    async def race_get_run(self, run_id, user_id):
        # 模拟竞态：读取时 run 仍可见（删除尚未提交/未生效）
        return await orig_get_run(self, run_id, user_id)

    monkeypatch.setattr(repo_mod.AgentRunRepository, "get_run", race_get_run)

    # 删除获胜：会话先变成 tombstone（模拟在 get_run 与插入之间提交）
    async with client._factory() as s:
        sess = (await s.execute(select(ChatSession).where(ChatSession.id == "sess-fb"))).scalar_one()
        sess.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        sess.title = None
        await s.commit()

    # 屏障锁内复核 tombstone：拒绝反馈，不留孤儿
    r = await client.post("/api/v1/chat/agent/feedback",
                          headers=_auth(USER_A),
                          json={"run_id": "run-fb", "rating": 5})
    assert r.status_code == 404

    async with client._factory() as s:
        from app.models.agent_run import AgentFeedback
        assert (await s.execute(select(AgentFeedback))).scalars().all() == []


@pytest.mark.asyncio
async def test_feedback_ok_for_active_session(client):
    """正常路径：活跃会话的 run 反馈成功。"""
    from app.models.agent_run import AgentRun

    async with client._factory() as s:
        s.add(ChatSession(id="sess-ok", user_id=USER_A, title="会话"))
        s.add(AgentRun(id="run-ok", user_id=USER_A, session_id="sess-ok",
                       query="q", status="completed"))
        await s.commit()

    r = await client.post("/api/v1/chat/agent/feedback",
                          headers=_auth(USER_A),
                          json={"run_id": "run-ok", "rating": 4})
    assert r.status_code == 200


# ---------- P1 #7 / #8：迁移与任务源码守卫 ----------

def test_008_downgrade_guard_source():
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "008_document_index_generation.py"
    src = p.read_text(encoding="utf-8")
    down = src.split("def downgrade()", 1)[1]
    assert "active_generation" in down
    assert down.index("active_generation") < down.index("drop_column")
    import re
    assert re.search(r"raise\s+\w+", down)


def test_sync_note_vector_disposes_stale_connections():
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "app" / "tasks" / "celery_app.py"
    src = p.read_text(encoding="utf-8")
    assert "dispose_stale_connections" in src


# ---------- P1 #9：R6C 心跳 lease + limit ----------

@pytest.mark.asyncio
async def test_heartbeat_lease_and_limit(env):
    """心跳新鲜的运行不回收；心跳过期的回收；limit 实际生效（最旧优先）。"""
    from app.models.agent_run import AgentRun
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    fresh_hb = now - timedelta(minutes=1)
    stale_hb = now - timedelta(minutes=20)

    async with env.factory() as s:
        # created_at 都很老：旧规则（仅按 created_at）会全部误回收
        s.add(AgentRun(id="run-hb-fresh", user_id="u1", query="q", status="started",
                       created_at=now - timedelta(minutes=30), heartbeat_at=fresh_hb))
        s.add(AgentRun(id="run-hb-stale", user_id="u1", query="q", status="started",
                       created_at=now - timedelta(minutes=30), heartbeat_at=stale_hb))
        s.add(AgentRun(id="run-no-hb", user_id="u1", query="q", status="started",
                       created_at=now - timedelta(minutes=30)))
        s.add(AgentRun(id="run-no-hb2", user_id="u1", query="q", status="started",
                       created_at=now - timedelta(minutes=29)))
        await s.commit()

    cutoff = now - timedelta(minutes=15)
    async with env.factory() as s:
        repo = AgentRunRepository(s)
        reclaimed = await repo.reclaim_stale_runs(cutoff, limit=1)
        await s.commit()
        # 心跳新鲜的 run-hb-fresh 不可回收；候选为 stale/no-hb/no-hb2，
        # limit=1 且最旧优先 → 只回收 run-hb-stale（created 30min，早于 no-hb 30min? 相同——
        # 但 run-hb-stale 与 run-no-hb 同为 30min；任取其一，总数必须为 1）
        assert reclaimed == 1

    async with env.factory() as s:
        states = {r.id: r.status for r in (await s.execute(select(AgentRun))).scalars()}
        assert states["run-hb-fresh"] == "started"          # 心跳新鲜：不回收
        assert states["run-no-hb2"] == "started"            # limit=1：未被波及
        reclaimed_ids = {k for k, v in states.items() if v == "timeout"}
        assert len(reclaimed_ids) == 1
        assert "run-hb-fresh" not in reclaimed_ids

    # 再次回收（limit 放大）：剩余两个 stale 候选全部收敛
    async with env.factory() as s:
        repo = AgentRunRepository(s)
        assert await repo.reclaim_stale_runs(cutoff, limit=10) == 2
        await s.commit()


@pytest.mark.asyncio
async def test_touch_run_heartbeat(env):
    from app.models.agent_run import AgentRun
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with env.factory() as s:
        s.add(AgentRun(id="run-touch", user_id="u1", query="q", status="started",
                       created_at=now - timedelta(minutes=5)))
        await s.commit()

    async with env.factory() as s:
        repo = AgentRunRepository(s)
        await repo.touch_run_heartbeat("run-touch")
        await s.commit()

    async with env.factory() as s:
        run = (await s.execute(select(AgentRun).where(AgentRun.id == "run-touch"))).scalar_one()
        assert run.heartbeat_at is not None


# ---------- P1 #6：v1 上传收编 generation ----------

@pytest.mark.asyncio
async def test_v1_upload_registers_generation(env, monkeypatch):
    """v1 上传：登记 document_index 行，携带代次写入，发布推进 active_generation。"""
    from app.router.knowledge_service import KnowledgeService

    recorded = {}

    async def fake_get_document(files=None, user_id=None, progress_callback=None,
                                space_id="", generation=None):
        recorded["generation"] = generation
        return None

    env.store.get_document = fake_get_document

    class _Up:
        filename = "v1doc.txt"
        size = 5

        async def read(self, size=-1):
            return "v1数据".encode("utf-8")

        async def seek(self, pos):
            return None

    svc = KnowledgeService()
    out = await svc.handle_add_vector_single(_Up(), "u-v1", space_id="sp-x")
    assert out == "v1doc.txt"
    assert recorded["generation"] == 1  # 首次上传分配代次 1

    async with env.factory() as s:
        rows = (await s.execute(
            select(DocumentIndexStatus) if False else select(
                __import__("app.models.document_index", fromlist=["DocumentIndex"]).DocumentIndex)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == DocumentIndexStatus.INDEXED
        assert rows[0].active_generation == 1
        assert rows[0].generation == 1
        assert rows[0].space_id == "sp-x"

    # 读取侧可见：v1 文档对 Agent 检索可见
    import app.services.document_index_service as dis
    async with env.factory() as s:
        row = (await s.execute(
            select(__import__("app.models.document_index", fromlist=["DocumentIndex"]).DocumentIndex)
        )).scalar_one()
    active = await dis.get_active_document_md5s("u-v1", [row.md5])
    assert active == {row.md5: 1}


@pytest.mark.asyncio
async def test_v1_upload_failure_marks_failed(env, monkeypatch):
    """v1 向量写入失败：登记行条件落 index_failed，不留 indexed 幽灵行。"""
    from app.router.knowledge_service import KnowledgeService

    async def boom(**kwargs):
        raise RuntimeError("chroma down")

    env.store.get_document = boom

    class _Up:
        filename = "v1fail.txt"
        size = 5

        async def read(self, size=-1):
            return "v1失败".encode("utf-8")

        async def seek(self, pos):
            return None

    svc = KnowledgeService()
    with pytest.raises(Exception):
        await svc.handle_add_vector_single(_Up(), "u-v1f", space_id="")

    async with env.factory() as s:
        DocumentIndex = __import__("app.models.document_index", fromlist=["DocumentIndex"]).DocumentIndex
        row = (await s.execute(select(DocumentIndex))).scalar_one()
        assert row.status == DocumentIndexStatus.INDEX_FAILED
        assert row.active_generation == 0  # 未发布


# ---------- #6 读取侧 gen-less 规则收紧 ----------

@pytest.mark.asyncio
async def test_retrieval_filter_genless_rule(env, monkeypatch):
    """gen-less 切片：纯存量文档（active=0）放行；已发布代次文档的残留丢弃。"""
    from app.rag.retrieval_service import RetrievalService
    import app.services.document_index_service as dis

    async def _active(user_id, md5s):
        return {"M-GEN": 2, "M-LEGACY": 0}

    monkeypatch.setattr(dis, "get_active_document_md5s", _active)

    svc = RetrievalService(user_id="u1")
    docs = [
        NS(page_content="当前代次", metadata={"md5": "M-GEN", "generation": 2}),
        NS(page_content="残留", metadata={"md5": "M-GEN"}),          # active>0 无代次 → 丢弃
        NS(page_content="纯存量", metadata={"md5": "M-LEGACY"}),      # active=0 无代次 → 放行
    ]
    kept = await svc._filter_active_documents(docs)
    assert [d.page_content for d in kept] == ["当前代次", "纯存量"]
