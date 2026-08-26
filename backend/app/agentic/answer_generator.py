"""
带引用的答案生成器 -- 基于证据生成答案并标注引用。

职责：
- 根据证据生成答案
- 在答案中标注引用来源
- 确保答案仅基于提供的证据
- 生成后用 LLM-as-judge 对答案质量评分（faithfulness/completeness/relevance）
"""
from typing import List, Dict, Any, Optional
import asyncio
import json
import re

from app.core.logger_handler import logger
from app.rag.retrieval_service import Evidence
from app.agentic.citation import citation_manager


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

            # LLM-as-judge 质量评分（失败不影响主流程）
            quality_scores = await self._judge_answer(query, context, answer)

            return {
                "answer": answer,
                "citations": citation_manager.format_citations_for_display(citations),
                "quality_scores": quality_scores,
            }

        except asyncio.TimeoutError:
            logger.error("【答案生成】超时")
            return {
                "answer": "抱歉，生成答案超时，请稍后再试。",
                "citations": citation_manager.format_citations_for_display(citations),
                "quality_scores": None,
            }
        except Exception as e:
            import traceback
            logger.error(f"【答案生成】失败: {e}\n{traceback.format_exc()}")
            return {
                "answer": "抱歉，生成答案时出现错误。",
                "citations": [],
                "quality_scores": None,
            }

    async def _judge_answer(
        self,
        query: str,
        context: str,
        answer: str,
    ) -> Optional[Dict[str, Any]]:
        """
        用 LLM-as-judge 对生成的答案进行质量评分。

        评估四维度：faithfulness（忠实度）、completeness（完整度）、
        relevance（相关性）、overall（综合）。

        :param query: 用户原始查询
        :param context: 喂给生成器的证据上下文
        :param answer: 生成的答案
        :return: {"faithfulness_score": float, "completeness_score": float,
                  "relevance_score": float, "overall_score": float,
                  "issues": list, "suggestions": list}，失败返回 None
        """
        try:
            chat_model = self._get_chat_model()
            prompt = self._build_judge_prompt(query, context, answer)

            from langchain_core.messages import HumanMessage
            response = await asyncio.wait_for(
                chat_model.ainvoke([HumanMessage(content=prompt)]),
                timeout=15.0,
            )

            raw = response.content.strip()
            # 剥离可能的 markdown 代码块包裹
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            scores = json.loads(raw)
            logger.info(
                f"【LLM-as-judge】评分: faithfulness={scores.get('faithfulness_score')}, "
                f"completeness={scores.get('completeness_score')}, "
                f"overall={scores.get('overall_score')}"
            )
            return scores
        except asyncio.TimeoutError:
            logger.warning("【LLM-as-judge】评分超时，跳过")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"【LLM-as-judge】JSON 解析失败: {e}")
            return None
        except Exception as e:
            logger.warning(f"【LLM-as-judge】评分失败: {e}")
            return None

    def _build_judge_prompt(self, query: str, context: str, answer: str) -> str:
        """构建 LLM-as-judge 评估提示词。"""
        return f"""你是一个回答质量评估专家。请根据以下标准评估回答质量。

## 用户问题
{query}

## 参考上下文
{context if context else "（无上下文）"}

## 模型回答
{answer}

## 评估标准
回答应准确、完整、基于上下文

## 请评估
1. 回答是否基于上下文（faithfulness）
2. 回答是否完整覆盖问题（completeness）
3. 回答是否包含无关信息（relevance）
4. 回答语言是否自然流畅

请输出 JSON 格式：
{{
  "faithfulness_score": 0-1,
  "completeness_score": 0-1,
  "relevance_score": 0-1,
  "overall_score": 0-1,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1", "建议2"]
}}"""

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
5. 如果不同证据之间存在矛盾（例如旧版建议和新版建议冲突），请优先采用最新版本的信息（如文档标题或内容中带年份/新版/新建议/2023/2024 等标识的），并简要说明旧版已被新版取代
6. 对于数值型推荐（如连接池大小、索引数量），给出明确结论而非罗列多个选项

{citation_instruction}

证据：
{context}

用户问题：{query}

请回答："""


# 全局工厂
def create_answer_generator(llm_config: dict = None) -> AnswerGenerator:
    """创建答案生成器实例"""
    return AnswerGenerator(llm_config=llm_config)
