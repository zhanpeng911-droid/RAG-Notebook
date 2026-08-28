"""app/agent/agent.py 测试 —— 工厂组装、非流式与 SSE 流式执行。

create_tool_calling_agent / AgentExecutor 在模块命名空间替换为可控假类，
用假 executor 的 astream 迭代器驱动完整对话轮（输出/中间步骤/异常）。
"""
import json
import sys
from types import SimpleNamespace as NS

import pytest

# langsmith 的 @traceable 在装饰时已被包装；运行时读 langchain_core.__version__
# 会撞上 conftest 的 MagicMock——给它补版本号即可绕过，无需取消包装
_lc = sys.modules.get("langchain_core")
if _lc is not None and not isinstance(getattr(_lc, "__version__", None), str):
    _lc.__version__ = "0.3.0"

import app.agent.agent as agent_mod  # noqa: E402


def _install_langsmith_fakes(monkeypatch):
    """langsmith RunTree 运行时 from langchain_core.callbacks.manager import ...，
    补假子模块条目并随 monkeypatch 自动还原（避免跨文件污染）。"""
    import types as _t
    from unittest.mock import MagicMock as _MM
    subs = {
        "langchain_core.callbacks": {},
        "langchain_core.callbacks.manager": {"AsyncCallbackManager": _MM,
                                             "CallbackManager": _MM},
        "langchain_core.runnables": {"RunnableConfig": _MM,
                                     "ensure_config": _MM},
        "langchain_core.tracers": {},
        "langchain_core.tracers.langchain": {"LangChainTracer": _MM},
    }
    for sub, attrs in subs.items():
        if sub not in sys.modules:
            m = _t.ModuleType(sub)
            for k, v in attrs.items():
                setattr(m, k, v)
            monkeypatch.setitem(sys.modules, sub, m)
from app.agent.agent import AgentFactory, agent_factory, get_agent_response, get_agent_stream_response  # noqa: E402


class FakeExecutor:
    """记录构造参数，astream 返回预设 chunk 序列。"""

    instances = []

    def __init__(self, **kwargs):
        type(self).instances.append(kwargs)

    async def astream(self, inputs):
        for chunk in self._chunks:
            yield chunk
        # 模拟流式结束（若非空序列则结束）
        return


class _Tooling:
    def __init__(self, factory=None, chunks=None, executor_cls=None):
        self.factory = factory
        self.chunks = chunks or []
        self.executor_cls = executor_cls or FakeExecutor


@pytest.fixture()
def fake_env(monkeypatch):
    """替换 agent 模块的模型/工具构造点。"""
    box = {"factory": None, "chunks": [], "set_users": [], "executor_cls": None}

    class FakeExecutorImpl(FakeExecutor):
        pass

    box["executor_cls"] = FakeExecutorImpl

    async def fake_astream(self, inputs):
        for chunk in box["chunks"]:
            yield chunk

    FakeExecutorImpl.astream = fake_astream
    FakeExecutorImpl._chunks_provider = None

    monkeypatch.setattr(agent_mod, "create_tool_calling_agent",
                        lambda *a, **k: "agent-object")
    monkeypatch.setattr(agent_mod, "AgentExecutor", FakeExecutorImpl)

    fake_factory = NS()
    fake_factory.default_system_prompt = "系统提示"
    fake_factory.create_agent_executor = lambda **k: FakeExecutorImpl(
        **k) if False else _make_executor(box)

    def _make_executor(b):
        ex = FakeExecutorImpl()
        ex._chunks = b["chunks"]
        return ex

    fake_factory.create_agent_executor = lambda **k: _make_executor(box)
    box["factory"] = fake_factory
    monkeypatch.setattr(agent_mod, "agent_factory", fake_factory)

    # @traceable(langsmith) 在 mock 环境读 langchain_core.__version__ 会崩，
    # 换为 identity 跳过追踪
    monkeypatch.setattr(agent_mod, "traceable", lambda f: f)

    # 上下文 set/reset 记录（不实际改 ContextVar）
    def _set_user(uid):
        box["set_users"].append(uid)

    monkeypatch.setattr(agent_mod, "set_current_user_id", _set_user)
    monkeypatch.setattr(agent_mod, "reset_current_user_id", lambda: None)
    return box


def _chunk_output(text):
    return {"output": text}


def _chunk_step(log, tool, tool_input, obs):
    action = NS(log=log, tool=tool, tool_input=tool_input)
    return {"intermediate_steps": [(action, obs)]}


# ---------- 工厂 ----------

def test_factory_default_tools_and_prompt():
    # 模块级单例已在 mock 环境下构造成功
    assert isinstance(agent_factory, AgentFactory)
    assert len(agent_factory.default_tools) == 8
    assert agent_factory.default_system_prompt == "test prompt"  # mock load_prompt


def test_create_agent_executor_llm_config_usable(fake_env, monkeypatch):
    import app.utils.factory as fmod
    monkeypatch.setattr(fmod, "sanitize_client_llm_config", lambda c: c)
    monkeypatch.setattr(fmod, "llm_config_is_usable", lambda c: True)
    monkeypatch.setattr(fmod, "create_chat_model_from_config",
                        lambda c: NS(name="m"))

    agent_factory.create_agent_executor(
        llm_config={"provider": "custom"}, custom_tools=["t1"])
    kwargs = FakeExecutor.instances[-1]
    assert kwargs["tools"] == ["t1"]
    assert kwargs["return_intermediate_steps"] is True


def test_create_agent_executor_llm_config_unusable_raises(fake_env, monkeypatch):
    import app.utils.factory as fmod
    monkeypatch.setattr(fmod, "sanitize_client_llm_config", lambda c: c)
    monkeypatch.setattr(fmod, "llm_config_is_usable", lambda c: False)
    with pytest.raises(ValueError):
        agent_factory.create_agent_executor(llm_config={})


# ---------- 非流式 ----------

@pytest.mark.asyncio
async def test_get_agent_response_collects_output_and_steps(fake_env):
    fake_env["chunks"] = [
        _chunk_step("思考1", "search_notes_tool", {"query": "x"}, "结果1"),
        _chunk_output("最终"),
        _chunk_output("回答"),
    ]
    out = await get_agent_response("问题", user_id="u1")
    assert out["response"] == "最终回答"
    assert out["steps"][0]["tool"] == "search_notes_tool"
    assert fake_env["set_users"] == ["u1"]


@pytest.mark.asyncio
async def test_get_agent_response_empty_output_fallback(fake_env):
    fake_env["chunks"] = [_chunk_step("t", "w", {}, "o")]
    out = await get_agent_response("q")
    assert out["response"] == "抱歉，我无法理解您的请求。"


@pytest.mark.asyncio
async def test_get_agent_response_exception_message(fake_env, monkeypatch):
    async def boom(self, inputs):
        raise RuntimeError("模型不可用")
        yield  # 使其成为 async generator，在迭代时抛出
    FakeExecutor.astream = boom
    monkeypatch.setattr(agent_mod, "AgentExecutor", FakeExecutor)
    fake_env["factory"].create_agent_executor = lambda **k: FakeExecutor()

    out = await get_agent_response("q")
    assert "出现了错误" in out["response"]
    assert "模型不可用" in out["response"]


# ---------- 流式 SSE ----------

class FakeSessionManager:
    def __init__(self):
        self.history = []
        self.added = []

    async def get_history(self, session_id, user_id):
        return list(self.history)

    async def add_message(self, session_id, user_id, query, response):
        self.added.append((session_id, user_id, query, response))


@pytest.fixture()
def stream_env(fake_env, monkeypatch):
    _install_langsmith_fakes(monkeypatch)
    fake_env["sm"] = FakeSessionManager()
    class FakeSM:
        session_manager = fake_env["sm"]
    monkeypatch.setattr(agent_mod, "sm", FakeSM())
    monkeypatch.setattr(agent_mod, "set_thinking_callback", lambda cb: None)
    monkeypatch.setattr(agent_mod, "set_llm_config", lambda cfg: None)
    monkeypatch.setattr(agent_mod, "reset_llm_config", lambda: None)
    return fake_env


@pytest.mark.asyncio
async def test_stream_normal_flow(stream_env):
    stream_env["chunks"] = [_chunk_output("你好"), _chunk_output("世界")]
    events = [json.loads(e.split("data: ", 1)[1])
              async for e in get_agent_stream_response(
                  "q", "sess-1", "u1", llm_config={})]
    types = [e["type"] for e in events]
    assert types[0] == "response"           # 初始空响应
    assert "response" in types              # 逐字符输出
    assert types[-1] == "done"
    assert stream_env["sm"].added[0][:3] == ("sess-1", "u1", "q")


@pytest.mark.asyncio
async def test_stream_thinking_events_pushed(stream_env):
    stream_env["chunks"] = [_chunk_output("答")]
    events = []
    async for e in get_agent_stream_response("q", "s", "u"):
        events.append(json.loads(e.split("data: ", 1)[1]))
    assert any(ev["type"] == "response" for ev in events)


@pytest.mark.asyncio
async def test_stream_agent_error_emits_error_event(stream_env, monkeypatch):
    async def boom(self, inputs):
        raise RuntimeError("执行中断")
        yield
    FakeExecutor.astream = boom
    stream_env["factory"].create_agent_executor = lambda **k: FakeExecutor()

    events = [json.loads(e.split("data: ", 1)[1])
              async for e in get_agent_stream_response("q", "s", "u")]
    types = [e["type"] for e in events]
    assert "error" in types
    assert "执行中断" in [e.get("content", "") for e in events if e["type"] == "error"][0]
    assert "done" in types
