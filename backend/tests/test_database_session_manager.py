"""database_session_manager 直接单测 —— 会话历史 CRUD、归属校验、级联删除。

AsyncSessionLocal monkeypatch 为内存 SQLite；直接调用管理器方法。
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.models.chat_history import Base, ChatSession, ChatMessage
from app.models.agent_run import AgentRun, AgentStep, AgentFeedback  # noqa: F401  注册 agent 表
from app.services.database_session_manager import DatabaseSessionManager
from fastapi import HTTPException as FE

USER_A = "u-aaaa-0000-0000-000000000001"
USER_B = "u-bbbb-0000-0000-000000000002"


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=engine, class_=AsyncSession,
                             expire_on_commit=False)
    import importlib
    m = importlib.import_module("app.services.database_session_manager")
    monkeypatch.setattr(m, "AsyncSessionLocal", fac)
    # 种子：A 的会话 + 两条消息
    async with fac() as s:
        s.add(ChatSession(id="s1", user_id=USER_A, title="A会话"))
        s.add(ChatMessage(id=1, session_id="s1", role="user", content="问"))
        s.add(ChatMessage(id=2, session_id="s1", role="assistant", content="答"))
        await s.commit()
    yield fac
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_session_own_returns_pairs(factory):
    mgr = DatabaseSessionManager()
    out = await mgr.get_session("s1", USER_A)
    assert out["history"] == [("问", "答")]


@pytest.mark.asyncio
async def test_get_session_other_user_403(factory):
    mgr = DatabaseSessionManager()
    with pytest.raises(FE) as ei:
        await mgr.get_session("s1", USER_B)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_get_session_missing_404(factory):
    mgr = DatabaseSessionManager()
    with pytest.raises(FE) as ei:
        await mgr.get_session("s-nope", USER_A)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_history(factory):
    mgr = DatabaseSessionManager()
    assert await mgr.get_history("s1", USER_A) == [("问", "答")]


@pytest.mark.asyncio
async def test_add_message_existing_session(factory):
    mgr = DatabaseSessionManager()
    await mgr.add_message("s1", USER_A, "问2", "答2")
    out = await mgr.get_session("s1", USER_A)
    assert out["history"] == [("问", "答"), ("问2", "答2")]


@pytest.mark.asyncio
async def test_add_message_creates_new_session(factory):
    mgr = DatabaseSessionManager()
    await mgr.add_message("s-new", USER_A, "新问题", "新答案")
    out = await mgr.get_session("s-new", USER_A)
    assert out["history"] == [("新问题", "新答案")]
    async with factory() as db:
        s = (await db.execute(
            __import__("sqlalchemy").select(ChatSession).where(ChatSession.id == "s-new"))).scalar_one()
        assert s.title == "新问题"  # 默认标题被首个问题替换


@pytest.mark.asyncio
async def test_add_message_other_user_403(factory):
    mgr = DatabaseSessionManager()
    with pytest.raises(FE) as ei:
        await mgr.add_message("s1", USER_B, "x", "y")
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_clear_session_existing(factory):
    mgr = DatabaseSessionManager()
    await mgr.clear_session("s1", USER_A)
    with pytest.raises(FE) as ei:
        await mgr.get_session("s1", USER_A)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_clear_session_missing_noop(factory):
    mgr = DatabaseSessionManager()
    await mgr.clear_session("s-nope", USER_A)  # 不抛错


@pytest.mark.asyncio
async def test_clear_session_cascades_agent_related_data(factory):
    """删除会话必须同时清理关联的 Agent 运行/步骤/反馈（无外键级联，须显式删除）。"""
    async with factory() as db:
        run = AgentRun(id="run-1", user_id=USER_A, session_id="s1", query="q")
        db.add(run)
        await db.flush()
        db.add(AgentStep(run_id="run-1", user_id=USER_A, phase="retrieving"))
        db.add(AgentFeedback(run_id="run-1", user_id=USER_A, rating=5))
        await db.commit()

    mgr = DatabaseSessionManager()
    await mgr.clear_session("s1", USER_A)

    async with factory() as db:
        from sqlalchemy import select
        assert (await db.execute(select(AgentRun).where(AgentRun.id == "run-1"))).scalar_one_or_none() is None
        assert (await db.execute(select(AgentStep).where(AgentStep.run_id == "run-1"))).scalar_one_or_none() is None
        assert (await db.execute(select(AgentFeedback).where(AgentFeedback.run_id == "run-1"))).scalar_one_or_none() is None
        assert (await db.execute(select(ChatMessage).where(ChatMessage.session_id == "s1"))).scalars().all() == []
        assert (await db.execute(select(ChatSession).where(ChatSession.id == "s1"))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_clear_session_other_user_does_not_delete(factory):
    """B 不能删除 A 的会话（clear_session 内部校验归属）。"""
    mgr = DatabaseSessionManager()
    await mgr.clear_session("s1", USER_B)  # 不属于 B：不删除
    out = await mgr.get_session("s1", USER_A)  # A 的会话仍在
    assert out["history"] == [("问", "答")]


@pytest.mark.asyncio
async def test_get_all_session_ids(factory):
    mgr = DatabaseSessionManager()
    assert await mgr.get_all_session_ids() == ["s1"]
    assert await mgr.get_all_session_ids(USER_B) == []
    assert await mgr.get_all_session_ids(USER_A) == ["s1"]


@pytest.mark.asyncio
async def test_get_user_sessions(factory):
    mgr = DatabaseSessionManager()
    sessions = await mgr.get_user_sessions(USER_A)
    assert sessions[0]["id"] == "s1"
    assert sessions[0]["title"] == "A会话"
