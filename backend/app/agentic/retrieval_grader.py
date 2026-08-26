"""
证据质量评估器 —— 判断检索到的证据是否足以回答问题。

职责：
- 评估证据与查询的相关性
- 判断证据是否充分
- 决定是否需要改写查询并重新检索
"""
from typing import List
from dataclasses import dataclass

from app.core.logger_handler import logger
from app.core.runtime_config import get as get_runtime_config
from app.rag.retrieval_service import Evidence


@dataclass
class GradingResult:
    """评估结果"""
    is_sufficient: bool
    confidence: float  # 0-1
    confidence_level: str  # "high" / "medium" / "low" / "none"
    reason: str
    relevant_evidences: List[Evidence]


class EvidenceGrader:
    """
    证据质量评估器。

    使用规则引擎快速评估，避免额外的 LLM 调用。
    阈值参数支持运行时热更新（grader.min_relevance / grader.confidence_*）。
    """

    def _confidence_level(self, confidence: float) -> str:
        """将置信度映射为分级，供 CRAG 纠错回路决策。"""
        if confidence >= get_runtime_config("grader.confidence_high"):
            return "high"
        elif confidence >= get_runtime_config("grader.confidence_medium"):
            return "medium"
        elif confidence >= get_runtime_config("grader.confidence_low"):
            return "low"
        else:
            return "none"

    def grade(
        self,
        query: str,
        evidences: List[Evidence],
        retrieval_round: int = 0,
    ) -> GradingResult:
        """
        评估证据质量。

        :param query: 用户查询
        :param evidences: 检索到的证据列表
        :param retrieval_round: 当前检索轮次
        :return: 评估结果
        """
        if not evidences:
            return GradingResult(
                is_sufficient=False,
                confidence=0.0,
                confidence_level="none",
                reason="未检索到任何证据",
                relevant_evidences=[],
            )

        # 过滤低相关性证据
        min_relevance = get_runtime_config("grader.min_relevance")
        relevant = [
            ev for ev in evidences
            if ev.score >= min_relevance
        ]

        if not relevant:
            return GradingResult(
                is_sufficient=False,
                confidence=0.1,
                confidence_level="none",
                reason="所有证据的相关性分数过低",
                relevant_evidences=[],
            )

        # 计算置信度
        avg_score = sum(ev.score for ev in relevant) / len(relevant)
        coverage = len(relevant) / max(len(evidences), 1)
        confidence = (avg_score * 0.7 + coverage * 0.3)

        # 判断是否充分（confidence_medium 同时作为"最低可用置信度"阈值）
        is_sufficient = (
            len(relevant) >= 1
            and confidence >= get_runtime_config("grader.confidence_medium")
        )

        # 第二轮检索时放宽标准
        if retrieval_round > 0 and len(relevant) > 0:
            is_sufficient = True

        reason = self._generate_reason(is_sufficient, len(relevant), avg_score, retrieval_round)
        conf_level = self._confidence_level(confidence)

        logger.info(
            f"【证据评估】sufficient={is_sufficient}, confidence={confidence:.2f} ({conf_level}), "
            f"relevant={len(relevant)}/{len(evidences)}, round={retrieval_round}"
        )

        return GradingResult(
            is_sufficient=is_sufficient,
            confidence=confidence,
            confidence_level=conf_level,
            reason=reason,
            relevant_evidences=relevant,
        )

    def _generate_reason(
        self, is_sufficient: bool, count: int, avg_score: float, round: int
    ) -> str:
        """生成评估原因"""
        if is_sufficient:
            return f"找到 {count} 个相关证据，平均相关性 {avg_score:.2f}"
        else:
            if count == 0:
                return "未找到相关证据，建议改写查询"
            else:
                return f"证据质量不足（{count} 个，平均相关性 {avg_score:.2f}），建议重新检索"


# 全局实例
evidence_grader = EvidenceGrader()
