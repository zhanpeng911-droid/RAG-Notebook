"""
Agent 中间件 —— 为 LangChain Agent 提供生命周期钩子。

钩子类型：
- before_agent / after_agent: Agent 运行前后
- before_model / after_model: 模型调用前后
- wrap_model_call / wrap_tool_call: 模型/工具调用包装

所有钩子都记录日志，便于调试 Agent 的执行流程。
"""
from langchain.agents import AgentState
from langchain.agents.middleware import wrap_tool_call, wrap_model_call, after_model, before_model, after_agent, \
    before_agent
from langgraph.runtime import Runtime

from app.core.logger_handler import logger


@before_agent
def log_before_agent(status: AgentState, runtime: Runtime):
    """Agent 启动前的日志记录"""
    logger.info(f"[before_agent] agent启动， 输入：{status['messages']}， 共{len(status['messages'])}条消息")


@after_agent
def log_after_agent(status: AgentState, runtime: Runtime):
    """Agent 运行结束后的日志记录"""
    logger.info(f"[after_agent] agent运行结束， 输出：{status['messages']}， 共{len(status['messages'])}条消息")

@before_model
def log_before_model(status: AgentState, runtime: Runtime):
    """模型调用前的日志记录"""
    logger.info(f"[before_model] model启动， 输入：{status['messages']}， 共{len(status['messages'])}条消息")


@after_model
def log_after_model(status: AgentState, runtime: Runtime):
    """模型调用后的日志记录"""
    logger.info(f"[after_model] model运行结束， 输出：{status['messages']}， 共{len(status['messages'])}条消息")

@wrap_model_call
def model_call_hook(request, handler):
    """模型调用包装 —— 记录调用日志"""
    logger.info("模型调用了")
    return handler(request)

@wrap_tool_call
def tool_call_hook(request, handler):
    """工具调用包装 —— 记录工具名称和参数"""
    logger.info(f"工具{request.tool_call['name']}调用了, 传入参数{request.tool_call['args']}")
    return handler(request)


def get_middleware():
    """返回本模块的所有中间件 —— 用于 AgentFactory 初始化"""
    return [
        log_before_agent,
        log_after_agent,
        log_before_model,
        log_after_model,
        model_call_hook,
        tool_call_hook,
    ]
