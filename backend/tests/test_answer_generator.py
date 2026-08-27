"""AnswerGenerator 测试 —— 无证据拒答、引用生成、超时/异常降级与 judge。

聊天模型直接注入实例属性（_chat_model）绕开 factory；LLM-as-judge
与生成共用同一假模型，验证四维度解析与 JSON 容错。
"""
import asyncio
import json

import pytest

from app.agentic.answer_generator import AnswerGenerator, create_answer_generator
from app.rag.retrieval_service import Evidence


def _ev(source_type="knowledge", source_id="k1", title="资料.pdf",
        content="Redis 默认 everysec 策略保证数据持久性"):
    return Evidence(source_type=source_type, source_id=source_id,
                    chunk_id="c1", title=title, content=content, score=0.9,
                    metadata={"user_id": "u1"})


class StubModel:
    """可编程模型：按调用序号返回预设响应或抛异常。"""

    def __init__(self, responses=None, errors=None):
        self.responses = responses or []
        self.errors = errors or []
        self.calls = []
        self.idx = 0

    async def ainvoke(self, messages):
        self.calls.append(messages)
        i = self.idx
        self.idx += 1
        if i < len(self.errors):
            err = self.errors[i]
            raise err
        text = self.responses[i] if i < len(self.responses) else "默认答案"
        return type("Resp", (), {"content": text})()


# ---------- 拒答 ----------

@pytest.mark.asyncio
async def test_no_evidence_refuses_without_model():
    gen = AnswerGenerator()
    out = await gen.generate("什么是 X", [])
    assert "没有找到足够的证据" in out["answer"]
    assert out["citations"] == []
    assert gen._chat_model is None          # 未触发懒加载


# ---------- 正常生成 ----------

@pytest.mark.asyncio
async def test_generate_with_citations_and_judge():
    gen = AnswerGenerator()
    model = StubModel(responses=[
        "根据 [1]，Redis 默认 everysec。",
        json.dumps({"faithfulness_score": 0.9, "completeness_score": 0.8,
                    "relevance_score": 0.7, "overall_score": 0.8,
                    "issues": [], "suggestions": []}),
    ])
    gen._chat_model = model

    out = await gen.generate("Redis 默认策略", [_ev(), _ev(source_id="k2",
                                                            title="另一篇.pdf",
                                                            content="补充内容")])
    assert out["answer"].startswith("根据 [1]")
    assert len(out["citations"]) >= 1
    assert out["quality_scores"]["faithfulness_score"] == 0.9
    assert model.idx == 2                    # 生成 + judge 各一次


# ---------- 降级 ----------

@pytest.mark.asyncio
async def test_generate_timeout_returns_graceful_message():
    gen = AnswerGenerator()
    gen._chat_model = StubModel(errors=[asyncio.TimeoutError()])
    out = await gen.generate("q", [_ev()])
    assert "超时" in out["answer"]
    assert out["quality_scores"] is None


@pytest.mark.asyncio
async def test_generate_exception_returns_error_message():
    gen = AnswerGenerator()
    gen._chat_model = StubModel(errors=[RuntimeError("模型挂了")])
    out = await gen.generate("q", [_ev()])
    assert "出现错误" in out["answer"]


# ---------- judge 容错 ----------

@pytest.mark.asyncio
async def test_judge_parses_markdown_wrapped_json():
    gen = AnswerGenerator()
    raw = json.dumps({"overall_score": 0.6, "issues": ["x"]})
    gen._chat_model = StubModel(responses=[f"```json\n{raw}\n```"])
    scores = await gen._judge_answer("q", "ctx", "答案")
    assert scores["overall_score"] == 0.6


@pytest.mark.asyncio
async def test_judge_invalid_json_returns_none():
    gen = AnswerGenerator()
    gen._chat_model = StubModel(responses=["这不是 JSON"])
    assert await gen._judge_answer("q", "ctx", "a") is None


@pytest.mark.asyncio
async def test_judge_timeout_returns_none():
    gen = AnswerGenerator()
    gen._chat_model = StubModel(errors=[asyncio.TimeoutError()])
    assert await gen._judge_answer("q", "c", "a") is None


# ---------- 纯函数 ----------

def test_build_context_prefixes_sources():
    gen = AnswerGenerator()
    ctx = gen._build_context([_ev(title="A.pdf"),
                              _ev(source_type="note", title="B.md")])
    assert "[来源1: 知识库《A.pdf》]" in ctx
    assert "[来源2: 笔记《B.md》]" in ctx


def test_build_prompt_citation_instruction():
    gen = AnswerGenerator()
    p_with = gen._build_prompt("q", "ctx", "【引用说明】", True)
    assert "使用 [数字] 格式引用来源" in p_with
    p_without = gen._build_prompt("q", "ctx", "【引用说明】", False)
    assert "使用 [数字] 格式引用来源" not in p_without


def test_factory_returns_instance():
    gen = create_answer_generator({"llm": "custom"})
    assert isinstance(gen, AnswerGenerator)
    assert gen.llm_config == {"llm": "custom"}
