"""
Agent 模块 -- 基于 LangChain 的 AI 智能代理。

核心组件：
- AgentFactory: 工厂类，负责创建 AgentExecutor 实例
- get_agent_response(): 获取 Agent 响应（非流式）
- get_agent_stream_response(): 获取 Agent 流式响应（SSE）

支持的 LLM 后端：
- 阿里云百炼（DashScope）
- Ollama（本地部署）
- OpenAI 兼容接口（DeepSeek 等）

Agent 可调用的工具：
- search_notes_tool: 语义搜索笔记
- get_note_stats_tool: 笔记统计
- get_today_reviews_tool: 获取今日回顾列表
- mark_reviewed_tool: 标记已回顾
- create_note_tool: 创建笔记
- get_related_notes_tool: 关联笔记推荐
- what_time_is_now: 获取当前时间
- get_user_info_tools: 获取用户信息
"""
import json
import asyncio
from langsmith import traceable
from typing import List, Optional, AsyncGenerator

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool

from app.agent.agent_middleware import get_middleware
from app.agent.agent_tools import what_time_is_now, get_user_info_tools, \
    search_notes_tool, get_note_stats_tool, get_today_reviews_tool, mark_reviewed_tool, \
    create_note_tool, get_related_notes_tool, set_current_user_id, reset_current_user_id, \
    set_thinking_callback, set_llm_config, reset_llm_config
from app.core.logger_handler import logger
from app.services import session_manager as sm
from app.utils.prompt_loader import load_prompt


class AgentFactory:
    """
    Agent 工厂类 —— 负责创建 AgentExecutor 实例。

    设计模式：工厂模式
    - 每次调用 create_agent_executor() 都会创建全新的实例
    - 支持动态注入工具、模型、提示词
    - 避免状态污染，保证请求隔离
    """

    def __init__(
            self,
            model: str = "qwen3-max",
            api_key: Optional[str] = None,
            default_tools: Optional[List[BaseTool]] = None,
            default_middleware: Optional[List] = None,
            default_system_prompt: Optional[str] = None,
    ):
        """
        初始化工厂配置（仅配置，不创建实例）。

        :param model: 默认模型名称
        :param api_key: 默认 API Key（不传则从 .env 读取）
        :param default_tools: 默认工具列表（不传则使用内置工具）
        :param default_middleware: 默认中间件列表（日志钩子）
        :param default_system_prompt: 默认系统提示词
        """
        self.model = model
        from app.config.validator import get_settings
        self.api_key = api_key or get_settings().CHAT_API_KEY
        self.default_tools = default_tools or self._get_default_tools()
        self.default_middleware = default_middleware or self._get_default_middleware()
        self.default_system_prompt = default_system_prompt or self._get_default_system_prompt()

    @staticmethod
    def _get_default_tools() -> List[BaseTool]:
        """
        获取默认工具列表 -- Agent 可调用的所有工具。

        工具说明：
        - search_notes_tool: 语义搜索笔记
        - get_note_stats_tool: 笔记统计
        - get_today_reviews_tool: 获取今日回顾列表
        - mark_reviewed_tool: 标记笔记已回顾
        - create_note_tool: 创建新笔记
        - get_related_notes_tool: 关联笔记推荐
        - what_time_is_now: 获取当前时间
        - get_user_info_tools: 获取用户信息
        """
        return [
            what_time_is_now,
            get_user_info_tools,
            search_notes_tool,
            get_note_stats_tool,
            get_today_reviews_tool,
            mark_reviewed_tool,
            create_note_tool,
            get_related_notes_tool,
        ]

    def _get_default_middleware(self) -> List:
        """获取默认中间件 —— Agent 生命周期日志钩子"""
        return get_middleware()

    @staticmethod
    def _get_default_system_prompt() -> str:
        """获取默认系统提示词 —— 从 prompt.yaml 配置加载"""
        return load_prompt('main_prompt')

    def _create_chat_model(self, custom_model: Optional[str] = None):
        """
        根据 settings.LLM_TYPE 创建聊天模型实例。

        委托给 factory.create_chat_model_from_settings()，消除重复逻辑。

        :param custom_model: 自定义模型名称（覆盖 settings 配置）
        :return: LangChain 兼容的聊天模型实例
        """
        from app.utils.factory import create_chat_model_from_settings
        return create_chat_model_from_settings(custom_model)

    def _create_prompt(self, custom_system_prompt: Optional[str] = None) -> ChatPromptTemplate:
        """
        创建提示词模板。

        模板结构：
        1. system: 系统提示词（定义 Agent 角色和行为）
        2. chat_history: 历史对话（MessagesPlaceholder 动态填充）
        3. human: 用户输入
        4. agent_scratchpad: Agent 思考过程（工具调用中间步骤）

        :param custom_system_prompt: 自定义系统提示词（覆盖默认）
        :return: ChatPromptTemplate 实例
        """
        return ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad")
        ])

    def create_agent_executor(
            self,
            custom_tools: Optional[List[BaseTool]] = None,
            custom_model: Optional[str] = None,
            custom_system_prompt: Optional[str] = None,
            llm_config: Optional[dict] = None,
            verbose: bool = True,
            return_intermediate_steps: bool = True,
            **kwargs
    ) -> AgentExecutor:
        """
        核心工厂方法 —— 创建全新的 AgentExecutor 实例。

        创建流程：
        1. 创建聊天模型（优先使用前端传入的 llm_config，否则走 .env）
        2. 创建提示词模板
        3. 组装工具列表
        4. 调用 create_tool_calling_agent() 创建 Agent
        5. 包装成 AgentExecutor（支持工具调用循环）

        :param custom_tools: 自定义工具列表（覆盖默认）
        :param custom_model: 自定义模型名称（仅 env 模式）
        :param custom_system_prompt: 自定义系统提示词（覆盖默认）
        :param llm_config: 前端传入的 LLM 配置
            - provider: deepseek/openai/anthropic/ollama/custom
            - model: 模型名称
            - api_key: API 密钥
            - base_url: API 地址
            - protocol: openai/anthropic
        :param verbose: 是否打印详细日志
        :param return_intermediate_steps: 是否返回中间步骤（工具调用记录）
        :return: AgentExecutor 实例
        """
        # 1. 创建模型：优先使用前端传入的 llm_config（生产会剥离客户端 api_key）
        if llm_config is not None:
            from app.utils.factory import (
                create_chat_model_from_config,
                llm_config_is_usable,
                sanitize_client_llm_config,
            )
            llm_config = sanitize_client_llm_config(llm_config)
            if not llm_config_is_usable(llm_config):
                raise ValueError("请先在设置页面配置 AI 模型（云模型需要 API Key，Ollama 仅需本地服务可用）")
            chat_model = create_chat_model_from_config(llm_config)
        else:
            # 未传 llm_config → 回退到 .env 默认配置
            chat_model = self._create_chat_model(custom_model)

        prompt = self._create_prompt()
        tools = custom_tools or self.default_tools
        # TODO(bug): system_prompt 计算后未传入 AgentExecutor，疑似业务遗漏，另行跟踪
        _system_prompt = custom_system_prompt or self.default_system_prompt

        # 2. 创建 Agent（支持工具调用的智能代理）
        agent = create_tool_calling_agent(chat_model, tools, prompt)

        # 3. 创建 Executor（管理 Agent 执行循环）
        return AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            **kwargs
        )


# 初始化全局工厂配置
agent_factory = AgentFactory()


async def get_agent_response(
        query: str,
        history: Optional[List[tuple]] = None,
        user_id: Optional[str] = None,
        custom_tools: Optional[List[BaseTool]] = None,
        **kwargs
):
    """
    获取 Agent 响应（非流式）。

    执行流程：
    1. 设置当前用户ID到上下文（供工具函数使用）
    2. 从工厂创建全新的 AgentExecutor 实例
    3. 将历史对话转换为 LangChain 消息格式
    4. 调用 agent_executor.astream() 执行 Agent
    5. 收集最终回答和中间步骤（工具调用记录）

    :param query: 用户查询文本
    :param history: 会话历史 [(user_msg, assistant_msg), ...]
    :param user_id: 用户ID（用于工具函数获取用户数据）
    :param custom_tools: 自定义工具（可选，覆盖默认工具列表）
    :param kwargs: 传递给 AgentExecutor 的其他参数
    :return: {"response": "最终回答", "steps": [{"thought": "思考", "tool": "工具名", ...}]}
    """
    if user_id:
        set_current_user_id(user_id)

    try:
        # 1. 从工厂获取全新的 Executor 实例
        agent_executor = agent_factory.create_agent_executor(custom_tools=custom_tools, **kwargs)

        # 2. 构建聊天历史
        chat_history: List[BaseMessage] = []
        if history:
            from langchain_core.messages import HumanMessage, AIMessage
            for user_msg, assistant_msg in history:
                chat_history.append(HumanMessage(content=user_msg))
                chat_history.append(AIMessage(content=assistant_msg))

        # 3. 流式执行
        full_response = []
        steps = []
        async for chunk in agent_executor.astream({
            "input": query,
            "chat_history": chat_history,
            "system_prompt": agent_factory.default_system_prompt
        }):
            if "output" in chunk:
                full_response.append(chunk["output"])
            elif "intermediate_steps" in chunk:
                for action, observation in chunk["intermediate_steps"]:
                    # 记录日志
                    logger.info(f"\n\n🧠 [Agent 思考] {action.log}")
                    logger.info(f"🛠️ [调用工具] {action.tool}")
                    logger.info(f"📥 [工具输入] {action.tool_input}")
                    logger.info(f"📤 [工具结果] {observation}\n")
                    # 收集步骤
                    steps.append({
                        "thought": action.log,
                        "tool": action.tool,
                        "tool_input": action.tool_input,
                        "tool_output": observation
                    })

        return {
            "response": "".join(full_response) if full_response else "抱歉，我无法理解您的请求。",
            "steps": steps
        }

    except Exception as e:
        logger.error(f"Agent 执行错误: {str(e)}", exc_info=True)
        return {
            "response": f"抱歉，处理您的请求时出现了错误: {str(e)}",
            "steps": []
        }

@traceable
async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: Optional[List[BaseTool]] = None,
        llm_config: Optional[dict] = None,
        **kwargs
) -> AsyncGenerator[str, None]:
    """
    获取 Agent 流式响应（SSE）—— 支持思考过程实时推送。

    执行流程：
    1. 创建异步队列 thinking_queue，用于收集思考事件
    2. 启动后台任务 run_agent() 执行 Agent
    3. 主协程持续监听队列，实时推送思考事件到前端
    4. Agent 完成后，推送最终回答（逐字符流式）
    5. 发送结束标记 done

    SSE 事件格式：
    - {"type": "thinking", "stage": "hyde", "content": "..."}  思考过程
    - {"type": "response", "content": "字符"}                  回答内容
    - {"type": "error", "content": "错误信息"}                 错误
    - {"type": "done", "session_id": "..."}                    结束标记

    :param query: 用户查询文本
    :param session_id: 会话 ID（用于获取历史和保存对话）
    :param user_id: 用户 ID
    :param custom_tools: 自定义工具（可选）
    :param llm_config: 前端 LLM 配置
    :return: SSE 事件流生成器
    """
    
    thinking_queue = asyncio.Queue()
    agent_result_holder = {"response": None, "error": None}
    agent_done = asyncio.Event()
    
    async def thinking_callback(data: dict):
        """思考过程回调函数，将事件放入队列"""
        logger.info(f"【思考过程】{data.get('stage', 'unknown')}: {data.get('content', '')}")
        await thinking_queue.put(data)
    
    async def run_agent():
        """在独立任务中执行 Agent"""
        try:
            set_current_user_id(user_id)
            set_thinking_callback(thinking_callback)
            set_llm_config(llm_config)
            
            history = await sm.session_manager.get_history(session_id, user_id)
            logger.info(f"【Agent流式响应】获取会话历史成功，历史记录数: {len(history)}")
            
            chat_history: List[BaseMessage] = []
            if history:
                from langchain_core.messages import HumanMessage, AIMessage
                for user_msg, assistant_msg in history:
                    chat_history.append(HumanMessage(content=user_msg))
                    chat_history.append(AIMessage(content=assistant_msg))
            
            agent_executor = agent_factory.create_agent_executor(
                custom_tools=custom_tools, llm_config=llm_config, **kwargs
            )
            
            full_response = []
            
            async for chunk in agent_executor.astream({
                "input": query,
                "chat_history": chat_history,
                "system_prompt": agent_factory.default_system_prompt
            }):
                if "output" in chunk:
                    full_response.append(chunk["output"])
                elif "intermediate_steps" in chunk:
                    for action, observation in chunk["intermediate_steps"]:
                        logger.info(f"\n\n🧠 [Agent 思考] {action.log}")
                        logger.info(f"🛠️ [调用工具] {action.tool}")
                        logger.info(f"📥 [工具输入] {action.tool_input}")
                        logger.info(f"📤 [工具结果] {observation}\n")
            
            agent_result_holder["response"] = "".join(full_response) if full_response else "抱歉，我无法理解您的请求。"
        except Exception as e:
            logger.error(f"【Agent流式响应】Agent执行失败: {e}", exc_info=True)
            agent_result_holder["error"] = str(e)
        finally:
            reset_current_user_id()
            reset_llm_config()
            agent_done.set()
    
    # 启动 Agent 执行任务
    agent_task = asyncio.create_task(run_agent())
    
    try:
        logger.info(f"【Agent流式响应】开始处理请求，用户ID: {user_id}, 会话ID: {session_id}, 查询: {query}")

        # 先发送初始响应
        yield f"data: {json.dumps({'type': 'response', 'content': '', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        
        # 持续监听队列并实时推送思考事件，同时等待 Agent 完成
        while not agent_done.is_set():
            try:
                # 使用短超时轮询队列，实现实时推送
                event = await asyncio.wait_for(thinking_queue.get(), timeout=0.1)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                thinking_queue.task_done()
            except asyncio.TimeoutError:
                # 超时是正常的，继续等待
                continue
        
        # Agent 已完成，推送队列中剩余的所有思考事件
        while not thinking_queue.empty():
            try:
                event = thinking_queue.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                thinking_queue.task_done()
            except asyncio.QueueEmpty:
                break
        
        # 等待 agent_task 完全结束
        await agent_task
        
        if agent_result_holder["error"]:
            error_message = f"错误: {agent_result_holder['error']}"
            yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return
        
        response = agent_result_holder["response"]
        
        # 添加到会话历史
        await sm.session_manager.add_message(session_id, user_id, query, response)
        logger.info("【Agent流式响应】添加到会话历史成功")
        
        # 发送回答内容
        for char in response:
            yield f"data: {json.dumps({'type': 'response', 'content': char}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.02)
        
        # 发送结束标记
        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        logger.info(f"【Agent流式响应】处理完成，会话ID: {session_id}")
        
    except Exception as e:
        logger.error(f"【Agent流式响应】处理请求失败: {e}", exc_info=True)
        
        # 取消 agent 任务
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
        
        error_message = f"错误: {str(e)}"
        yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
