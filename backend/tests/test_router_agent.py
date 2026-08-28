"""agent_router 测试 —— Agent 会话隔离：鉴权负路径、会话归属、运行记录、反馈。

独立 app 挂 agent_router，override get_db 为内存 SQLite；agent_query_stream
内部直接使用 AsyncSessionLocal，测试中将其 monkeypatch 指向同一内存工厂。
run_agent / run_agent_stream 打桩为可编程假实现，走完整路由逻辑。
"""
import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.config.validator import get_settings
from app.models.chat_history import Base, ChatSession

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"
USER_B = "u-bbbb-0000-0000-000000000002"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client(monkeypatch):
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.agent_router import agent_router
    import app.db.db_config as db_config

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    # agent_query_stream 内部 from app.db.db_config import AsyncSessionLocal
    monkeypatch.setattr(db_config, "AsyncSessionLocal", factory)

    app = FastAPI()
    app.include_router(agent_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 种子：A 的一个会话
    async with factory() as s:
        s.add(ChatSession(id="sess-1", user_id=USER_A, title="A的会话"))
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        yield c
    await engine.dispose()


async def _seed_run(factory, run_id, user_id, session_id=None, query="q"):
    from app.models.agent_run import AgentRun, AgentStep
    async with factory() as s:
        s.add(AgentRun(id=run_id, user_id=user_id, session_id=session_id,
                       query=query, status="completed", answer="答案"))
        s.add(AgentStep(run_id=run_id, user_id=user_id, phase="planning",
                        step_data={"plan": "x"}))
        await s.commit()


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.post("/api/v1/chat/agent/query",
                          json={"query": "你好"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.post("/api/v1/chat/agent/query",
                          headers={"Authorization": "Bearer junk"},
                          json={"query": "你好"})
    assert r.status_code == 401


# ---------- 会话隔离（共享会话归属） ----------

@pytest.mark.asyncio
async def test_query_with_unknown_session_404(client, monkeypatch):
    async def fake_run(**kwargs):
        return {"answer": "a", "citations": [], "error": None}
    monkeypatch.setattr("app.agentic.graph.run_agent", fake_run)

    r = await client.post("/api/v1/chat/agent/query",
                          headers=_auth(USER_A),
                          json={"query": "q", "session_id": "sess-nope"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_query_other_user_session_403(client, monkeypatch):
    async def fake_run(**kwargs):
        return {"answer": "a", "citations": [], "error": None}
    monkeypatch.setattr("app.agentic.graph.run_agent", fake_run)

    r = await client.post("/api/v1/chat/agent/query",
                          headers=_auth(USER_B),
                          json={"query": "q", "session_id": "sess-1"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_query_stream_other_user_session_403(client, monkeypatch):
    async def fake_stream(**kwargs):
        yield {"type": "completed", "answer": "a", "citations": []}
    monkeypatch.setattr("app.agentic.graph.run_agent_stream", fake_stream)

    r = await client.post("/api/v1/chat/agent/query/stream",
                          headers=_auth(USER_B),
                          json={"query": "q", "session_id": "sess-1"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_query_stream_unknown_session_404(client, monkeypatch):
    async def fake_stream(**kwargs):
        yield {"type": "completed", "answer": "a", "citations": []}
    monkeypatch.setattr("app.agentic.graph.run_agent_stream", fake_stream)

    r = await client.post("/api/v1/chat/agent/query/stream",
                          headers=_auth(USER_A),
                          json={"query": "q", "session_id": "sess-nope"})
    assert r.status_code == 404


# ---------- 非流式查询正路径 ----------

@pytest.mark.asyncio
async def test_query_success_creates_run(client, monkeypatch):
    seen = {}

    async def fake_run(**kwargs):
        seen.update(kwargs)
        return {"answer": "完整答案", "citations": [{"id": "c1"}], "error": None}
    monkeypatch.setattr("app.agentic.graph.run_agent", fake_run)

    r = await client.post("/api/v1/chat/agent/query",
                          headers=_auth(USER_A),
                          json={"query": "今天的笔记", "llm_config": {"api_key": "sk-x"}})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["answer"] == "完整答案"
    assert data["run_id"]
    assert seen["user_id"] == USER_A

    # 运行记录已落库
    run_id = data["run_id"]
    r2 = await client.get(f"/api/v1/chat/agent/runs/{run_id}",
                          headers=_auth(USER_A))
    assert r2.status_code == 200
    assert r2.json()["data"]["run"]["status"] == "completed"


@pytest.mark.asyncio
async def test_query_error_marks_run_failed(client, monkeypatch):
    async def fake_run(**kwargs):
        return {"answer": None, "citations": [], "error": "模型挂了"}
    monkeypatch.setattr("app.agentic.graph.run_agent", fake_run)

    r = await client.post("/api/v1/chat/agent/query",
                          headers=_auth(USER_A),
                          json={"query": "q"})
    assert r.status_code == 200
    assert r.json()["data"]["error"] == "模型挂了"


@pytest.mark.asyncio
async def test_query_new_session_created(client, monkeypatch):
    async def fake_run(**kwargs):
        return {"answer": "a", "citations": [], "error": None}
    monkeypatch.setattr("app.agentic.graph.run_agent", fake_run)

    r = await client.post("/api/v1/chat/agent/query",
                          headers=_auth(USER_A),
                          json={"query": "q", "space_id": "sp-1"})
    assert r.status_code == 200
    session_id = r.json()["data"]["session_id"]
    assert session_id


# ---------- 流式查询正路径 ----------

@pytest.mark.asyncio
async def test_query_stream_completed_events(client, monkeypatch):
    events = [
        {"type": "started", "msg": "开始"},
        {"type": "completed", "answer": "流式答案", "citations": [{"id": "c"}]},
    ]

    async def fake_stream(**kwargs):
        for ev in events:
            yield dict(ev)
    monkeypatch.setattr("app.agentic.graph.run_agent_stream", fake_stream)

    r = await client.post("/api/v1/chat/agent/query/stream",
                          headers=_auth(USER_A),
                          json={"query": "q", "session_id": "sess-1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = await r.aread()
    text = body.decode("utf-8")
    assert '"type": "started"' in text
    assert '"type": "completed"' in text
    assert '"run_id"' in text


@pytest.mark.asyncio
async def test_query_stream_error_event(client, monkeypatch):
    async def fake_stream(**kwargs):
        yield {"type": "error", "error": "超时"}
    monkeypatch.setattr("app.agentic.graph.run_agent_stream", fake_stream)

    r = await client.post("/api/v1/chat/agent/query/stream",
                          headers=_auth(USER_A),
                          json={"query": "q"})
    assert r.status_code == 200
    text = (await r.aread()).decode("utf-8")
    assert '"type": "error"' in text
    assert "超时" in text


# ---------- 运行记录 ----------

@pytest.mark.asyncio
async def test_get_run_own(client):
    await _seed_run(client._factory, "run-1", USER_A)
    r = await client.get("/api/v1/chat/agent/runs/run-1", headers=_auth(USER_A))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["run"]["query"] == "q"
    assert data["steps"][0]["phase"] == "planning"


@pytest.mark.asyncio
async def test_get_run_other_user_404(client):
    await _seed_run(client._factory, "run-2", USER_A)
    r = await client.get("/api/v1/chat/agent/runs/run-2", headers=_auth(USER_B))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_run_missing_404(client):
    r = await client.get("/api/v1/chat/agent/runs/run-nope",
                         headers=_auth(USER_A))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_runs_own_only(client):
    await _seed_run(client._factory, "run-3", USER_A, session_id="sess-1")
    await _seed_run(client._factory, "run-4", USER_B)
    r = await client.get("/api/v1/chat/agent/runs", headers=_auth(USER_A))
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()["data"]["runs"]]
    assert ids == ["run-3"]


@pytest.mark.asyncio
async def test_list_runs_filter_session(client):
    await _seed_run(client._factory, "run-5", USER_A, session_id="sess-1")
    await _seed_run(client._factory, "run-6", USER_A, session_id="sess-other")
    r = await client.get("/api/v1/chat/agent/runs?session_id=sess-1",
                         headers=_auth(USER_A))
    ids = [x["id"] for x in r.json()["data"]["runs"]]
    assert ids == ["run-5"]


# ---------- 反馈 ----------

@pytest.mark.asyncio
async def test_feedback_success(client):
    await _seed_run(client._factory, "run-fb", USER_A)
    r = await client.post("/api/v1/chat/agent/feedback",
                          headers=_auth(USER_A),
                          json={"run_id": "run-fb", "rating": 5, "comment": "好"})
    assert r.status_code == 200
    assert r.json()["data"]["rating"] == 5


@pytest.mark.asyncio
async def test_feedback_run_missing_404(client):
    r = await client.post("/api/v1/chat/agent/feedback",
                          headers=_auth(USER_A),
                          json={"run_id": "run-nope", "rating": 3})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_feedback_rating_out_of_range_400(client):
    await _seed_run(client._factory, "run-fb2", USER_A)
    r = await client.post("/api/v1/chat/agent/feedback",
                          headers=_auth(USER_A),
                          json={"run_id": "run-fb2", "rating": 9})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_feedback_other_user_run_404(client):
    await _seed_run(client._factory, "run-fb3", USER_A)
    r = await client.post("/api/v1/chat/agent/feedback",
                          headers=_auth(USER_B),
                          json={"run_id": "run-fb3", "rating": 3})
    assert r.status_code == 404


# ---------- 工具函数 ----------

def test_redact_llm_config():
    from app.router.agent_router import _redact_llm_config
    out = _redact_llm_config({"api_key": "sk-secret", "model": "gpt"})
    assert out["api_key"] == "[REDACTED]"
    assert out["model"] == "gpt"
    assert _redact_llm_config(None) is None
    assert _redact_llm_config("not-dict") is None
