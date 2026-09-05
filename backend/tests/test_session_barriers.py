"""S05 会话删除屏障回归 —— tombstone 会话不得启动 Agent、迟到写入可被再次清理。

修复前缺陷：
- 已删（tombstone）会话仍能创建 run 并调用 provider；
- 重复删除直接跳过，历史竞态残留无法再次清除；
- 已删会话关联的 run 仍可通过 /runs 接口读回。
"""
import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from types import SimpleNamespace as NS

from app.config.validator import get_settings
from app.models.chat_history import Base, ChatSession, ChatMessage
from app.models.agent_run import AgentRun, AgentStep, AgentFeedback

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
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.agent_router import agent_router
    import app.db.db_config as db_config

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)
    monkeypatch.setattr(db_config, "AsyncSessionLocal", factory)
    # 会话管理器方法经模块级导入使用 AsyncSessionLocal，同样指向内存库
    import app.services.database_session_manager as dsm
    monkeypatch.setattr(dsm, "AsyncSessionLocal", factory)

    app = FastAPI()
    app.include_router(agent_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    # S01 就绪门槛：路由要求会话管理器已初始化
    from app.services.database_session_manager import init_database_session_manager
    await init_database_session_manager()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        yield NS(client=c, factory=factory)
    await engine.dispose()


async def _seed_session(factory, session_id, user_id=USER_A,
                        deleted_at=None, title="会话"):
    async with factory() as s:
        s.add(ChatSession(id=session_id, user_id=user_id, title=title,
                          deleted_at=deleted_at))
        await s.commit()


async def _counts(factory, model, **filters):
    async with factory() as s:
        q = select(model)
        for k, v in filters.items():
            q = q.where(getattr(model, k) == v)
        return len((await s.execute(q)).scalars().all())


# ---------- tombstone 会话不得启动 Agent ----------

@pytest.mark.asyncio
async def test_query_rejects_tombstoned_session(env, monkeypatch):
    """已删会话请求非流式 Agent：404、provider 不被调用、不新增 run。"""
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"answer": "不该出现的答案", "citations": [], "error": None}

    monkeypatch.setattr("app.agentic.graph.run_agent", fake_run)

    from datetime import datetime, timezone
    tomb = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_session(env.factory, "sess-tomb", deleted_at=tomb)

    r = await env.client.post("/api/v1/chat/agent/query",
                              headers=_auth(USER_A),
                              json={"query": "q", "session_id": "sess-tomb"})
    assert r.status_code == 404
    assert calls == []
    assert await _counts(env.factory, AgentRun) == 0


@pytest.mark.asyncio
async def test_stream_rejects_tombstoned_session(env, monkeypatch):
    """已删会话请求流式 Agent：404、provider 不被调用、不新增 run。"""
    calls = []

    async def fake_stream(**kwargs):
        calls.append(kwargs)
        yield {"type": "completed", "answer": "x", "citations": []}

    monkeypatch.setattr("app.agentic.graph.run_agent_stream", fake_stream)

    from datetime import datetime, timezone
    tomb = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_session(env.factory, "sess-tomb2", deleted_at=tomb)

    r = await env.client.post("/api/v1/chat/agent/query/stream",
                              headers=_auth(USER_A),
                              json={"query": "q", "session_id": "sess-tomb2"})
    assert r.status_code == 404
    assert calls == []
    assert await _counts(env.factory, AgentRun) == 0


# ---------- 重复删除幂等清理残留 ----------

@pytest.mark.asyncio
async def test_repeat_delete_cleans_residue_under_tombstone(env):
    """对已是 tombstone 的会话再次删除：清理竞态残留的消息/运行数据。"""
    from app.services.database_session_manager import DatabaseSessionManager
    from datetime import datetime, timezone
    tomb = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_session(env.factory, "sess-res", deleted_at=tomb)

    # 模拟删除与写入竞态留下的残留
    async with env.factory() as s:
        s.add(ChatMessage(id=9001, session_id="sess-res", role="user", content="残留问"))
        s.add(ChatMessage(id=9002, session_id="sess-res", role="assistant", content="残留答"))
        s.add(AgentRun(id="run-res", user_id=USER_A, session_id="sess-res", query="q"))
        await s.flush()
        s.add(AgentStep(run_id="run-res", user_id=USER_A, phase="planning"))
        s.add(AgentFeedback(run_id="run-res", user_id=USER_A, rating=4))
        await s.commit()

    mgr = DatabaseSessionManager()
    await mgr.clear_session("sess-res", USER_A)  # 第二次删除：不得跳过

    assert await _counts(env.factory, ChatMessage, session_id="sess-res") == 0
    assert await _counts(env.factory, AgentRun, session_id="sess-res") == 0
    assert await _counts(env.factory, AgentStep, run_id="run-res") == 0
    assert await _counts(env.factory, AgentFeedback, run_id="run-res") == 0

    # 重复删除他人会话仍被拒绝（不误清理）
    await _seed_session(env.factory, "sess-res2", user_id=USER_B, deleted_at=tomb)
    async with env.factory() as s:
        s.add(ChatMessage(id=9003, session_id="sess-res2", role="user", content="B的"))
        await s.commit()
    await mgr.clear_session("sess-res2", USER_A)
    assert await _counts(env.factory, ChatMessage, session_id="sess-res2") == 1


# ---------- 已删会话的 run 不可读 ----------

@pytest.mark.asyncio
async def test_run_of_deleted_session_not_readable(env):
    """运行详情/列表必须排除关联已删除会话的运行。"""
    from datetime import datetime, timezone
    tomb = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_session(env.factory, "sess-del", deleted_at=tomb)
    async with env.factory() as s:
        s.add(AgentRun(id="run-del", user_id=USER_A, session_id="sess-del",
                       query="q", status="completed", answer="a"))
        await s.commit()
    # 无会话关联的 run 仍然可见（明确 session_id=None 的合同）
    async with env.factory() as s:
        s.add(AgentRun(id="run-free", user_id=USER_A, session_id=None,
                       query="q2", status="completed", answer="b"))
        await s.commit()

    r = await env.client.get("/api/v1/chat/agent/runs/run-del",
                             headers=_auth(USER_A))
    assert r.status_code == 404, "已删会话的 run 不得通过详情接口读回"

    r2 = await env.client.get("/api/v1/chat/agent/runs",
                              headers=_auth(USER_A))
    assert r2.status_code == 200
    ids = [x["id"] for x in r2.json()["data"]["runs"]]
    assert "run-del" not in ids
    assert "run-free" in ids


@pytest.mark.asyncio
async def test_feedback_on_deleted_session_run_rejected(env):
    """已删会话的 run 不得接受新反馈。"""
    from datetime import datetime, timezone
    tomb = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_session(env.factory, "sess-fb", deleted_at=tomb)
    async with env.factory() as s:
        s.add(AgentRun(id="run-fb", user_id=USER_A, session_id="sess-fb",
                       query="q", status="completed"))
        await s.commit()

    r = await env.client.post("/api/v1/chat/agent/feedback",
                              headers=_auth(USER_A),
                              json={"run_id": "run-fb", "rating": 5})
    assert r.status_code == 404


# ---------- 共享终态提交（finalize_agent_run） ----------

@pytest.mark.asyncio
async def test_finalize_completed_appends_history_once(env):
    """completed：同一事务写终态 + 追加两条消息；重复 completed 不重复追加。"""
    from app.services.agent_completion_service import finalize_agent_run

    await _seed_session(env.factory, "sess-fin")
    async with env.factory() as s:
        s.add(AgentRun(id="run-fin", user_id=USER_A, session_id="sess-fin",
                       query="问", status="started"))
        await s.commit()

    out = await finalize_agent_run(
        "run-fin", USER_A, status="completed", answer="答",
        total_time_ms=100, session_id="sess-fin",
        user_message="问", assistant_message="答",
    )
    assert out == {"run_updated": True, "messages_added": True}
    assert await _counts(env.factory, ChatMessage, session_id="sess-fin") == 2

    # 迟到的重复 completed：条件写被拒，不再追加历史
    out2 = await finalize_agent_run(
        "run-fin", USER_A, status="completed", answer="答",
        total_time_ms=200, session_id="sess-fin",
        user_message="问", assistant_message="答",
    )
    assert out2["run_updated"] is False
    assert out2["messages_added"] is False
    assert await _counts(env.factory, ChatMessage, session_id="sess-fin") == 2


@pytest.mark.asyncio
async def test_finalize_skips_history_when_session_deleted(env):
    """删除先获胜：终态照常落库，但历史不追加（不向 tombstone 写入）。"""
    from app.services.agent_completion_service import finalize_agent_run
    from datetime import datetime, timezone

    tomb = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_session(env.factory, "sess-fin2", deleted_at=tomb)
    async with env.factory() as s:
        s.add(AgentRun(id="run-fin2", user_id=USER_A, session_id="sess-fin2",
                       query="问", status="started"))
        await s.commit()

    out = await finalize_agent_run(
        "run-fin2", USER_A, status="completed", answer="答",
        total_time_ms=100, session_id="sess-fin2",
        user_message="问", assistant_message="答",
    )
    assert out["run_updated"] is True
    assert out["messages_added"] is False
    assert await _counts(env.factory, ChatMessage, session_id="sess-fin2") == 0


@pytest.mark.asyncio
async def test_finalize_run_terminal_failure_no_messages(env):
    """终态条件写失败（其它写者已占用）时不追加消息。"""
    from app.services.agent_completion_service import finalize_agent_run

    await _seed_session(env.factory, "sess-fin3")
    async with env.factory() as s:
        s.add(AgentRun(id="run-fin3", user_id=USER_A, session_id="sess-fin3",
                       query="问", status="cancelled"))
        await s.commit()

    out = await finalize_agent_run(
        "run-fin3", USER_A, status="completed", answer="答",
        total_time_ms=100, session_id="sess-fin3",
        user_message="问", assistant_message="答",
    )
    assert out["run_updated"] is False
    assert out["messages_added"] is False
    assert await _counts(env.factory, ChatMessage, session_id="sess-fin3") == 0
