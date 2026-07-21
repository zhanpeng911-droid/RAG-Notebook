"""
带引用的答案生成器 —— 基于证据生成答案并标注引用。

职责：
- 根据证据生成答案
- 在答案中标注引用来源
- 确保答案仅基于提供的证据
"""
from typing import List, Dict, Any, Optional
import asyncio

from app.core.logger_handler import logger
from app.rag.retrieval_service import Evidence
from app.agentic.citation import Citation, citation_manager


class AnswerGenerator:
    """带引用的答案生成器"""

    def __init__(self, llm_config: dict = None):
        """
        初始化答案生成器。

        :param llm_config: LLM 配置
        """
        self.llm_config = llm_config
        self._chat_model = None

    def _get_chat_model(self):
        """获取聊天模型（懒加载）"""
        if self._chat_model is None:
            from app.utils.factory import (
                create_chat_model_from_config,
                llm_config_is_usable,
                sanitize_client_llm_config,
                get_default_chat_model,
            )
            config = sanitize_client_llm_config(self.llm_config)
            if llm_config_is_usable(config):
                self._chat_model = create_chat_model_from_config(config)
            else:
                self._chat_model = get_default_chat_model()
        return self._chat_model

    async def generate(
        self,
        query: str,
        evidences: List[Evidence],
        include_citations: bool = True,
    ) -> Dict[str, Any]:
        """
        生成带引用的答案。

        :param query: 用户查询
        :param evidences: 证据列表
        :param include_citations: 是否在答案中包含引用标记
        :return: {"answer": str, "citations": list}
        """
        if not evidences:
            return {
                "answer": "抱歉，我没有找到足够的证据来回答您的问题。",
                "citations": [],
            }

        # 创建引用
        citations = citation_manager.create_citations(evidences)
        citations_text = citation_manager.format_citations_for_prompt(citations)

        # 构建上下文
        context = self._build_context(evidences)

        # 生成答案
        try:
            chat_model = self._get_chat_model()

            prompt = self._build_prompt(query, context, citations_text, include_citations)

            from langchain_core.messages import HumanMessage
            response = await asyncio.wait_for(
                chat_model.ainvoke([HumanMessage(content=prompt)]),
                timeout=30.0,
            )

            answer = response.content

            return {
                "answer": answer,
                "citations": citation_manager.format_citations_for_display(citations),
            }

        except asyncio.TimeoutError:
            logger.error("【答案生成】超时")
            return {
                "answer": "抱歉，生成答案超时，请稍后再试。",
                "citations": citation_manager.format_citations_for_display(citations),
            }
        except Exception as e:
            logger.error(f"【答案生成】失败: {e}")
            return {
                "answer": "抱歉，生成答案时出现错误。",
                "citations": [],
            }

    def _build_context(self, evidences: List[Evidence]) -> str:
        """构建上下文"""
        parts = []
        for i, ev in enumerate(evidences, 1):
            if ev.source_type == "knowledge":
                parts.append(f"[来源{i}: 知识库《{ev.title}》]\n{ev.content}")
            else:
                parts.append(f"[来源{i}: 笔记《{ev.title}》]\n{ev.content}")
        return "\n\n".join(parts)

    def _build_prompt(
        self,
        query: str,
        context: str,
        citations_text: str,
        include_citations: bool,
    ) -> str:
        """构建提示词"""
        citation_instruction = ""
        if include_citations and citations_text:
            citation_instruction = f"""
在回答中，请使用 [数字] 格式引用来源。例如：
- 根据 [1]，...
- 如 [2] 所述，...

{citations_text}
"""

        return f"""你是一个知识库助手。请基于以下证据回答用户的问题。

重要规则：
1. 仅基于提供的证据回答，不要编造信息
2. 如果证据不足以回答问题，请明确说明
3. 回答要准确、简洁、有帮助
4. 使用中文回答

{citation_instruction}

证据：
{context}

用户问题：{query}

请回答："""


# 全局工厂
def create_answer_generator(llm_config: dict = None) -> AnswerGenerator:
    """创建答案生成器实例"""
    return AnswerGenerator(llm_config=llm_config)
