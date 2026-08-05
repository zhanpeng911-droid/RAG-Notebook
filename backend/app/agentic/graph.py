"""
Agent 状态图 —— 编排 Agentic RAG 的完整工作流。

工作流：
用户问题 → 认证与输入校验 → 问题分类/检索计划 → 并行检索 → 证据质量评估
         → (足够) 生成带引用答案
         → (不足) 改写查询并补检索（最多 1~2 次）
         → SSE 流式输出答案、引用和摘要
"""
import asyncio
from typing import AsyncGenerator, Dict, Any, Optional

from app.core.logger_handler import logger
from app.rag.retrieval_service import RetrievalService, Evidence
from app.agentic.state import AgentState, AgentPhase
from app.agentic.planner import planner
from app.agentic.retrieval_grader import evidence_grader
from app.agentic.answer_generator import create_answer_generator
from app.agentic.citation import citation_manager
from app.agentic.guardrails import guardrails


class AgentGraph:
    """
    Agent 状态图 —— 编排 Agentic RAG 的完整工作流。

    使用方式:
        graph = AgentGraph(user_id="user123")
        async for event in graph.run("什么是机器学习？"):
            print(event)
    """

    def __init__(
        self,
        user_id: str,
        space_id: str = None,
        session_id: str = None,
        llm_config: dict = None,
    ):
        """
        初始化 Agent 图。

        :param user_id: 用户 ID
        :param space_id: 空间 ID
        :param session_id: 会话 ID
        :param llm_config: LLM 配置
        """
        self.user_id = user_id
        self.space_id = space_id
        self.session_id = session_id
        self.llm_config = llm_config

    async def run(self, query: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        执行 Agent 工作流，以 SSE 事件流的形式返回结果。

        :param query: 用户查询
        :yield: SSE 事件
        """
        # 初始化
        guardrails.start()

        state = AgentState(
            user_id=self.user_id,
            query=query,
            session_id=self.session_id,
            space_id=self.space_id,
        )

        # 输入校验
        if not guardrails.validate_user_id(self.user_id):
            yield self._create_event(AgentPhase.ERROR, error="无效的用户ID")
            return

        if not guardrails.validate_space_id(self.space_id):
            yield self._create_event(AgentPhase.ERROR, error="无效的空间ID")
            return

        # 清洗查询
        state.query = guardrails.sanitize_query(query)
        if not state.query:
            yield self._create_event(AgentPhase.ERROR, error="查询不能为空")
            return

        # 开始
        yield self._create_event(AgentPhase.STARTED, state=state)

        try:
            # 主循环：检索 -> 评估 -> (可能)重新检索
            while state.can_retry():
                # 检查超时
                if not guardrails.check_timeout():
                    logger.warning("【Agent】总超时")
                    state.error = "处理超时"
                    break

                # 规划
                yield self._create_event(AgentPhase.PLANNING, state=state)
                plan = planner.create_plan(
                    query=state.query,
                    user_id=self.user_id,
                    space_id=self.space_id,
                    retrieval_round=state.current_retrieval_round,
                )

                # 检索
                yield self._create_event(AgentPhase.RETRIEVING, state=state)
                retrieval_service = RetrievalService(
                    user_id=self.user_id,
                    space_id=self.space_id,
                    llm_config=self.llm_config,
                )

                evidences = await retrieval_service.retrieve(
                    query=state.query,
                    scope=plan.scope,
                    top_k=plan.top_k,
                    use_hyde=plan.use_hyde,
                    use_rerank=plan.use_rerank,
                )

                state.evidences = evidences
                yield self._create_event(AgentPhase.RETRIEVAL_COMPLETED, state=state)

                # 评估
                yield self._create_event(AgentPhase.GRADING_EVIDENCE, state=state)
                grading = evidence_grader.grade(
                    query=state.query,
                    evidences=evidences,
                    retrieval_round=state.current_retrieval_round,
                )

                if grading.is_sufficient:
                    # 证据充分，生成答案
                    break
                else:
                    # 证据不足，判断是否触发 CRAG 纠错回路
                    if not state.can_retry():
                        break

                    yield self._create_event(AgentPhase.REWRITING_QUERY, state=state)

                    # CRAG 纠错：置信度极低（none）时扩大检索范围
                    crag_expanded = False
                    if grading.confidence_level == "none" and state.current_retrieval_round == 0:
                        logger.info("【CRAG】置信度极低，触发纠错回路：扩大检索范围")
                        crag_expanded = True

                    state.query = planner.rewrite_query(
                        original_query=state.query,
                        failed_reason=grading.reason,
                    )
                    # CRAG 纠错：改写后扩大 scope 和 top_k
                    if crag_expanded:
                        # 临时扩大检索范围：下一轮 create_plan 会读取 retrieval_round
                        logger.info(f"【CRAG】改写查询: {state.query[:50]}, 扩大检索范围")
                    state.rewritten_queries.append(state.query)
                    state.increment_round()

            # 生成答案
            yield self._create_event(AgentPhase.GENERATING_ANSWER, state=state)

            answer_generator = create_answer_generator(llm_config=self.llm_config)
            result = await answer_generator.generate(
                query=query,  # 使用原始查询生成答案
                evidences=state.evidences,
            )

            state.answer = result["answer"]
            state.citations = result["citations"]
            quality_scores = result.get("quality_scores")

            yield self._create_event(AgentPhase.CITATION, state=state)

            # 完成
            state.phase = AgentPhase.COMPLETED
            yield self._create_event(
                AgentPhase.COMPLETED,
                state=state,
                quality_scores=quality_scores,
            )

        except Exception as e:
            logger.error(f"【Agent】执行失败: {e}", exc_info=True)
            state.error = str(e)
            yield self._create_event(AgentPhase.ERROR, state=state)

    def _create_event(
        self,
        phase: AgentPhase,
        state: AgentState = None,
        error: str = None,
        quality_scores: Any = None,
    ) -> Dict[str, Any]:
        """创建 SSE 事件"""
        event = {
            "type": phase.value,
            "timestamp": self._get_timestamp(),
        }

        if state:
            event["state"] = state.to_dict()

        if phase == AgentPhase.COMPLETED and state:
            event["answer"] = state.answer
            event["citations"] = state.citations
            if quality_scores is not None:
                event["quality_scores"] = quality_scores

        if phase == AgentPhase.ERROR:
            event["error"] = error or (state.error if state else "未知错误")

        return event

    def _get_timestamp(self) -> str:
        """获取时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()


async def run_agent(
    query: str,
    user_id: str,
    space_id: str = None,
    session_id: str = None,
    llm_config: dict = None,
) -> Dict[str, Any]:
    """
    运行 Agent 并返回最终结果（非流式）。

    :param query: 用户查询
    :param user_id: 用户 ID
    :param space_id: 空间 ID
    :param session_id: 会话 ID
    :param llm_config: LLM 配置
    :return: 最终结果
    """
    graph = AgentGraph(
        user_id=user_id,
        space_id=space_id,
        session_id=session_id,
        llm_config=llm_config,
    )

    result = {
        "answer": None,
        "citations": [],
        "quality_scores": None,
        "phases": [],
        "error": None,
    }

    async for event in graph.run(query):
        result["phases"].append(event)
        if event.get("answer"):
            result["answer"] = event["answer"]
        if event.get("citations"):
            result["citations"] = event["citations"]
        if event.get("quality_scores") is not None:
            result["quality_scores"] = event["quality_scores"]
        if event.get("error"):
            result["error"] = event["error"]

    return result


async def run_agent_stream(
    query: str,
    user_id: str,
    space_id: str = None,
    session_id: str = None,
    llm_config: dict = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    运行 Agent 并以 SSE 事件流的形式返回结果（流式）。

    :param query: 用户查询
    :param user_id: 用户 ID
    :param space_id: 空间 ID
    :param session_id: 会话 ID
    :param llm_config: LLM 配置
    :yield: SSE 事件
    """
    graph = AgentGraph(
        user_id=user_id,
        space_id=space_id,
        session_id=session_id,
        llm_config=llm_config,
    )

    async for event in graph.run(query):
        yield event
