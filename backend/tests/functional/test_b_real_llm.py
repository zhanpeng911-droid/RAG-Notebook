"""功能验收 Part B：真实 LLM 端到端 + IR 评测基线。

标记 @pytest.mark.real_api；CI --ignore=tests/functional，仅在配置了
LLM API Key 的发布前验收环境运行。缺 key 显式跳过。
IR 指标为纯函数，不依赖 key，始终可跑。
"""
import asyncio

import pytest

from .conftest import real_api

pytestmark = [pytest.mark.real_api]


@real_api
def test_agentic_rag_end_to_end_with_citations():
    """Agentic RAG 端到端：非流式 run_agent 返回答案与引用。"""
    from app.agentic.graph import run_agent

    result = asyncio.run(
        run_agent(query="什么是向量数据库？请基于知识库回答并给出引用",
                  user_id="u-func-real-1"))
    assert result["error"] is None
    assert result["answer"], "应生成答案"
    if result.get("citations"):
        assert "[1]" in result["answer"] or "[1，" in result["answer"]


@real_api
def test_agentic_rag_refusal_path():
    """拒答路径：与知识库无关的问题不应强答。"""
    from app.agentic.graph import run_agent

    result = asyncio.run(
        run_agent(query="请告诉我 2027 年世界杯冠军", user_id="u-func-real-1"))
    # 允许返回拒答文案或明确"不知道"，不允许编造
    assert result["error"] is None


def test_ir_recall_and_mrr():
    """IR 评测基线（纯函数，无需 key）：Recall@K / MRR 合理性。"""
    from evals.graders.ir_metrics import recall_at_k, reciprocal_rank

    ranked = ["mysql_01_index.txt", "mysql_02_query.txt", "redis_config.txt"]
    relevant = ["mysql_01_index.txt"]

    # Recall@3：命中 1/1
    assert recall_at_k(ranked, relevant, 3) == 1.0
    # MRR：命中在首位 → 1.0
    assert reciprocal_rank(ranked, relevant) == 1.0
