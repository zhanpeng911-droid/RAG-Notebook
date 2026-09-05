"""批次4C：Agent 统一 deadline / 结构化超时 / 终态条件写。"""
import asyncio
import time
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.agentic.graph as graph_mod
from app.agentic.answer_generator import create_answer_generator
from app.models.chat_history import Base
import app.models.agent_run  # noqa: F401  # 注册 agent_runs 等表到 Base.metadata


# ---------- 答案生成器：deadline 剩余预算 ----------

def test_generator_exhausted_deadline_returns_timed_out_without_calling_model():
    gen = create_answer_generator(llm_config={})
    # 用会抛错的 fake model：若被调用则测试失败
    calls = []

    class BoomModel:
        async def ainvoke(self, msgs):
            calls.append(1)
            raise AssertionError("deadline 耗尽不应启动模型请求")

    gen._chat_model = BoomModel()

    result = asyncio.run(gen.generate(
        query="q",
        evidences=[NS(source_type="note", title="t", content="c")],
        deadline=time.monotonic() - 10,  # 已过期
    ))
    assert result["timed_out"] is True
    assert result["error"] == "generation_timeout"
    assert calls == []


def test_generator_remaining_timeout_without_deadline_uses_default():
    gen = create_answer_generator(llm_config={})
    assert gen._remaining_timeout(None, default=30.0) == 30.0
    # 未过期时剩余预算为正
    assert gen._remaining_timeout(time.monotonic() + 10, default=30.0) > 0


# ---------- 图：预算耗尽在进入生成前结构化超时 ----------

@pytest.mark.asyncio
async def test_graph_budget_exhausted_yields_timeout_not_completed(monkeypatch):
    graph = graph_mod.AgentGraph(user_id="user-a")
    # 总预算为 0：run() 内 guardrails.start() 后 deadline 立即过期
    # （等价于前序检索已耗尽预算，不得进入答案生成）
    monkeypatch.setattr(graph_mod.Guardrails, "MAX_TOTAL_TIME", 0)

    # 若仍进入答案生成（bug），直接失败
    def _boom_factory(*a, **k):
        raise AssertionError("预算耗尽后不应创建答案生成器")
    monkeypatch.setattr(graph_mod, "create_answer_generator", _boom_factory)

    events = [e async for e in graph.run("测试查询")]

    types = [e["type"] for e in events]
    assert "timeout" in types
    assert "completed" not in types
    assert "generating_answer" not in types
    timeout_evt = next(e for e in events if e["type"] == "timeout")
    assert timeout_evt["error"] == "处理超时"


@pytest.mark.asyncio
async def test_retrieval_bounded_by_overall_budget(monkeypatch):
    """检索永久挂起时必须在整体预算内截断并发出 timeout，不能拖过总时限（F06）。"""
    from app.agentic.planner import planner

    graph = graph_mod.AgentGraph(user_id="user-a")
    monkeypatch.setattr(graph_mod.Guardrails, "MAX_TOTAL_TIME", 0.5)

    class FakePlan:
        query_type = NS(value="simple")
        scope = "all"
        top_k = 3
        use_hyde = False
        use_rerank = False

    monkeypatch.setattr(planner, "create_plan", lambda *a, **k: FakePlan())

    class HangRetrieval:
        async def retrieve(self, **k):
            await asyncio.sleep(10)  # 挂起，远超预算
            return []

    monkeypatch.setattr(graph_mod, "RetrievalService", lambda **k: HangRetrieval())

    start = time.monotonic()
    events = [e async for e in graph.run("测试")]
    elapsed = time.monotonic() - start

    types = [e["type"] for e in events]
    assert "timeout" in types
    assert elapsed < 5  # 不能拖 10 秒；受整体预算约束


@pytest.mark.asyncio
async def test_graph_normal_flow_still_completes(monkeypatch):
    """正常预算下流程不变：completed 事件照常发出。"""
    from app.agentic.planner import planner
    from app.agentic.retrieval_grader import evidence_grader

    graph = graph_mod.AgentGraph(user_id="user-a")

    class FakePlan:
        query_type = NS(value="simple")
        scope = "all"
        top_k = 3
        use_hyde = False
        use_rerank = False

    monkeypatch.setattr(planner, "create_plan", lambda *a, **k: FakePlan())
    monkeypatch.setattr(planner, "rewrite_query", lambda *a, **k: "rewritten")

    class FakeEv:
        source_type = "note"
        title = "t"
        content = "c"

    class FakeRetrieval:
        async def retrieve(self, **k):
            return [FakeEv()]

    monkeypatch.setattr(graph_mod, "RetrievalService", lambda **k: FakeRetrieval())
    monkeypatch.setattr(evidence_grader, "grade", lambda *a, **k: NS(
        confidence=0.9, confidence_level="high", is_sufficient=True,
        relevant_evidences=[FakeEv()], reason="ok"))

    class FakeGenerator:
        async def generate(self, **k):
            return {"answer": "答案", "citations": [], "quality_scores": None, "timed_out": False}

    monkeypatch.setattr(graph_mod, "create_answer_generator", lambda **k: FakeGenerator())

    events = [e async for e in graph.run("测试查询")]
    types = [e["type"] for e in events]
    assert "completed" in types
    assert "timeout" not in types
    completed = next(e for e in events if e["type"] == "completed")
    assert completed["answer"] == "答案"


# ---------- 运行记录：终态条件写 ----------

@pytest_asyncio.fixture
async def run_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_terminal_write_does_not_overwrite(run_db):
    from app.repositories.agent_run_repository import AgentRunRepository
    from app.models.agent_run import AgentRunStatus

    async with run_db() as s:
        repo = AgentRunRepository(s)
        run = await repo.create_run(user_id="u1", query="q")
        run_id = run.id
        await s.commit()

    async with run_db() as s:
        repo = AgentRunRepository(s)
        # 先完成
        ok = await repo.update_run_terminal(run_id, "completed", answer="final")
        await s.commit()
        assert ok is True

    async with run_db() as s:
        repo = AgentRunRepository(s)
        # 迟到的取消不得覆盖已完成结果
        ok = await repo.update_run_terminal(run_id, "cancelled", error_message="late cancel")
        await s.commit()
        assert ok is False
        run = await repo.get_run(run_id, "u1")
        assert run.status == AgentRunStatus.COMPLETED.value
        assert run.answer == "final"
        assert run.completed_at is not None


@pytest.mark.asyncio
async def test_repository_timout_sets_completed_at(run_db):
    from app.repositories.agent_run_repository import AgentRunRepository
    from app.models.agent_run import AgentRunStatus

    async with run_db() as s:
        repo = AgentRunRepository(s)
        run = await repo.create_run(user_id="u1", query="q")
        run_id = run.id
        await s.commit()

    async with run_db() as s:
        repo = AgentRunRepository(s)
        await repo.update_run_terminal(run_id, "timeout", error_message="处理超时")
        await s.commit()
        run = await repo.get_run(run_id, "u1")
        assert run.status == AgentRunStatus.TIMEOUT.value
        assert run.completed_at is not None


# ---------- S06：检索下限放大预算 ----------

@pytest.mark.asyncio
async def test_retrieval_tiny_budget_times_out_within_budget(monkeypatch):
    """剩余预算 30ms 时检索等待不得超过剩余预算（不得被 0.5s 下限放大）。"""
    from app.agentic.planner import planner

    graph = graph_mod.AgentGraph(user_id="user-a")
    monkeypatch.setattr(graph_mod.Guardrails, "MAX_TOTAL_TIME", 0.03)

    class FakePlan:
        query_type = NS(value="simple")
        scope = "all"
        top_k = 3
        use_hyde = False
        use_rerank = False

    monkeypatch.setattr(planner, "create_plan", lambda *a, **k: FakePlan())

    class HangRetrieval:
        async def retrieve(self, **k):
            await asyncio.sleep(10)
            return []

    monkeypatch.setattr(graph_mod, "RetrievalService", lambda **k: HangRetrieval())

    start = time.monotonic()
    events = [e async for e in graph.run("测试")]
    elapsed = time.monotonic() - start

    types = [e["type"] for e in events]
    assert "timeout" in types
    # 预算 0.03s + 调度容差；修复前会被 max(remaining, 0.5) 放大到 ~0.5s+
    assert elapsed < 0.35, f"检索耗时 {elapsed:.3f}s，超出 30ms 预算+容差"


# ---------- S06：judge 受共同 deadline 约束 ----------

@pytest.mark.asyncio
async def test_judge_cannot_exceed_deadline():
    """主生成很快、judge 挂起：总耗时不得超过 deadline（+容差），评分跳过。"""
    import time as _time
    from app.agentic.answer_generator import AnswerGenerator

    class FastGenHangJudge:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, msgs):
            self.calls += 1
            if self.calls == 1:
                return type("Resp", (), {"content": "答案"})()
            await asyncio.sleep(30)  # judge 挂起，远超剩余预算
            raise AssertionError("不应执行到这里")

    gen = AnswerGenerator()
    gen._chat_model = FastGenHangJudge()

    start = _time.monotonic()
    out = await gen.generate(
        query="q",
        evidences=[NS(source_type="note", source_id="k1", chunk_id="c1",
                      title="t", content="c", score=0.9, metadata={})],
        deadline=_time.monotonic() + 3.1,
    )
    elapsed = _time.monotonic() - start

    assert out["answer"] == "答案"       # 主答案按时完成
    assert out["timed_out"] is False
    assert out["quality_scores"] is None  # 评分跳过而非拖过总时限
    assert elapsed < 4.5, f"judge 超出共同 deadline：{elapsed:.2f}s（修复前 ~15s）"
    assert gen._chat_model.calls == 2


# ---------- S06：provider 异常 → 结构化失败，不得伪装成功 ----------

@pytest.mark.asyncio
async def test_generator_provider_error_structured_failure():
    """provider 抛错：返回结构化 failed（answer=None + error），不是道歉文案成功。"""
    from app.agentic.answer_generator import AnswerGenerator

    class BoomModel:
        async def ainvoke(self, msgs):
            raise RuntimeError("provider down")

    gen = AnswerGenerator()
    gen._chat_model = BoomModel()
    out = await gen.generate("q", [NS(source_type="note", source_id="k1", chunk_id="c1",
                                     title="t", content="c", score=0.9, metadata={})])
    assert out["answer"] is None
    assert out.get("error"), "provider 异常必须带结构化 error"
    assert out["timed_out"] is False or out.get("error") == "generation_failed"


@pytest.mark.asyncio
async def test_graph_provider_error_yields_error_not_completed(monkeypatch):
    """生成器报 error：graph 发 error 事件，不得发 completed。"""
    from app.agentic.planner import planner
    from app.agentic.retrieval_grader import evidence_grader

    graph = graph_mod.AgentGraph(user_id="user-a")

    class FakePlan:
        query_type = NS(value="simple")
        scope = "all"
        top_k = 3
        use_hyde = False
        use_rerank = False

    monkeypatch.setattr(planner, "create_plan", lambda *a, **k: FakePlan())

    class FakeEv:
        source_type = "note"
        title = "t"
        content = "c"

    class FakeRetrieval:
        async def retrieve(self, **k):
            return [FakeEv()]

    monkeypatch.setattr(graph_mod, "RetrievalService", lambda **k: FakeRetrieval())
    monkeypatch.setattr(evidence_grader, "grade", lambda *a, **k: NS(
        confidence=0.9, confidence_level="high", is_sufficient=True,
        relevant_evidences=[FakeEv()], reason="ok"))

    class BoomGenerator:
        async def generate(self, **k):
            return {"answer": None, "citations": [], "quality_scores": None,
                    "timed_out": False, "error": "generation_failed"}

    monkeypatch.setattr(graph_mod, "create_answer_generator", lambda **k: BoomGenerator())

    events = [e async for e in graph.run("测试查询")]
    types = [e["type"] for e in events]
    assert "error" in types
    assert "completed" not in types
