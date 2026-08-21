"""方案4 回归测试：Agentic RAG SSE 事件携带检索链路过程数据。

后端在 retrieving / retrieval_completed / rewriting_query / generating_answer
事件中透传 plan / retrieval / grading / rewrite 摘要，供前端 RetrievalTrace 渲染。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.retrieval_service import Evidence


def _evidence(score=0.9):
    return Evidence(
        source_type="knowledge",
        source_id="doc-1",
        chunk_id="chunk-1",
        title="mysql_01_index.txt",
        content="InnoDB 索引内容",
        score=score,
    )


def _sufficient_grading():
    grading = MagicMock()
    grading.is_sufficient = True
    grading.confidence = 0.85
    grading.confidence_level = "high"
    grading.reason = "证据充分"
    grading.relevant_evidences = [_evidence()]
    return grading


@pytest.mark.asyncio
async def test_sse_events_carry_trace_fields():
    """检索链路各阶段事件应透传 plan/retrieval/grading 过程摘要"""
    from app.agentic.graph import AgentGraph

    graph = AgentGraph(user_id="user-1")

    with patch("app.agentic.graph.RetrievalService") as mock_rs, \
         patch("app.agentic.graph.evidence_grader") as mock_grader, \
         patch("app.agentic.graph.create_answer_generator") as mock_gen_factory:
        mock_rs.return_value.retrieve = AsyncMock(return_value=[_evidence()])
        mock_grader.grade.return_value = _sufficient_grading()

        generator = MagicMock()
        generator.generate = AsyncMock(return_value={
            "answer": "答案",
            "citations": [],
            "quality_scores": None,
        })
        mock_gen_factory.return_value = generator

        events = [event async for event in graph.run("MySQL 回表是什么")]

    by_type = {e["type"]: e for e in events}

    # retrieving 事件带检索计划
    assert "retrieving" in by_type
    plan = by_type["retrieving"].get("plan")
    assert plan is not None
    assert plan["query_type"] in ("factual", "simple", "explanatory", "comparative", "procedural", "exploratory")
    assert isinstance(plan["top_k"], int) and plan["top_k"] > 0
    assert isinstance(plan["use_hyde"], bool)
    assert isinstance(plan["use_rerank"], bool)

    # retrieval_completed 事件带召回摘要
    retrieval = by_type["retrieval_completed"].get("retrieval")
    assert retrieval is not None
    assert retrieval["evidence_count"] == 1
    assert retrieval["top_k"] == plan["top_k"]

    # generating_answer 事件带证据评估摘要
    grading = by_type["generating_answer"].get("grading")
    assert grading is not None
    assert grading["is_sufficient"] is True
    assert grading["confidence_level"] == "high"
    assert 0 <= grading["confidence"] <= 1


@pytest.mark.asyncio
async def test_sse_rewrite_event_carries_grading_and_rewrite():
    """证据不足触发改写时，rewriting_query 事件应带评估结果与改写摘要"""
    from app.agentic.graph import AgentGraph

    graph = AgentGraph(user_id="user-1")

    insufficient = MagicMock()
    insufficient.is_sufficient = False
    insufficient.confidence = 0.05
    insufficient.confidence_level = "none"
    insufficient.reason = "未检索到任何证据"
    insufficient.relevant_evidences = []

    with patch("app.agentic.graph.RetrievalService") as mock_rs, \
         patch("app.agentic.graph.evidence_grader") as mock_grader, \
         patch("app.agentic.graph.create_answer_generator") as mock_gen_factory:
        mock_rs.return_value.retrieve = AsyncMock(return_value=[])
        # 第一轮不足（触发 CRAG），第二轮充分（退出循环）
        mock_grader.grade.side_effect = [insufficient, _sufficient_grading()]

        generator = MagicMock()
        generator.generate = AsyncMock(return_value={"answer": "答案", "citations": []})
        mock_gen_factory.return_value = generator

        events = [event async for event in graph.run("一个很长的不存在的问题内容")]

    rewrite_events = [e for e in events if e["type"] == "rewriting_query"]
    assert len(rewrite_events) == 1

    event = rewrite_events[0]
    assert event["grading"]["is_sufficient"] is False
    assert event["grading"]["confidence_level"] == "none"
    assert isinstance(event["rewrite"]["rewritten_query"], str)
    assert isinstance(event["rewrite"]["crag_triggered"], bool)


@pytest.mark.asyncio
async def test_sse_events_without_trace_fields_unchanged():
    """无过程数据的阶段事件（started/planning 等）不应携带空扩展字段"""
    from app.agentic.graph import AgentGraph

    graph = AgentGraph(user_id="user-1")

    with patch("app.agentic.graph.RetrievalService") as mock_rs, \
         patch("app.agentic.graph.evidence_grader") as mock_grader, \
         patch("app.agentic.graph.create_answer_generator") as mock_gen_factory:
        mock_rs.return_value.retrieve = AsyncMock(return_value=[_evidence()])
        mock_grader.grade.return_value = _sufficient_grading()

        generator = MagicMock()
        generator.generate = AsyncMock(return_value={"answer": "答案", "citations": []})
        mock_gen_factory.return_value = generator

        events = [event async for event in graph.run("什么是索引")]

    started = next(e for e in events if e["type"] == "started")
    planning = next(e for e in events if e["type"] == "planning")

    for event in (started, planning):
        assert "plan" not in event
        assert "retrieval" not in event
        assert "grading" not in event
        assert "rewrite" not in event
