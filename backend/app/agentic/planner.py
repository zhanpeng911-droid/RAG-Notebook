"""
问题分类与检索计划 —— 决定如何检索、检索什么。

职责：
- 分析用户问题类型（事实查询、解释性问题、比较问题等）
- 生成检索计划（scope、top_k、是否需要 HyDE）
- 生成改写查询（当证据不足时）
"""
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

from app.core.logger_handler import logger


class QueryType(str, Enum):
    """查询类型"""
    SIMPLE = "simple"           # 简单事实查询（"X的Y是什么"），走轻量路径
    FACTUAL = "factual"         # 事实查询（"什么是X"）
    EXPLANATORY = "explanatory" # 解释性问题（"为什么X"）
    COMPARATIVE = "comparative" # 比较问题（"X和Y的区别"）
    PROCEDURAL = "procedural"   # 步骤问题（"如何做X"）
    EXPLORATORY = "exploratory" # 探索性问题（"关于X的信息"）
    UNKNOWN = "unknown"


@dataclass
class RetrievalPlan:
    """检索计划"""
    query_type: QueryType
    scope: str  # knowledge | notes | all | space:{space_id}
    top_k: int
    use_hyde: bool
    use_rerank: bool
    rewritten_query: Optional[str] = None


class Planner:
    """
    检索计划器 —— 分析查询并生成检索计划。

    使用规则引擎（而非 LLM）进行快速分类，避免额外的模型调用。
    """

    # 关键词模式
    FACTUAL_KEYWORDS = ["什么是", "是什么", "定义", "含义", "概念", "意思"]
    EXPLANATORY_KEYWORDS = ["为什么", "原因", "原理", "解释", "为何"]
    COMPARATIVE_KEYWORDS = ["区别", "比较", "对比", "差异", "vs", "和", "与"]
    PROCEDURAL_KEYWORDS = ["如何", "怎么", "步骤", "方法", "教程", "操作"]

    # Adaptive-RAG: 简单查询判定阈值
    SIMPLE_MAX_LENGTH = 20  # 查询长度 <= 20 字符且含事实关键词 -> SIMPLE

    def classify_query(self, query: str) -> QueryType:
        """
        分类查询类型（Adaptive-RAG 风格：按复杂度路由）。

        SIMPLE 类型优先判定：短查询 + 事实关键词 -> 轻量路径（跳过 HyDE/rerank）。
        其余按 factual -> explanatory -> comparative -> procedural 顺序匹配。

        :param query: 用户查询
        :return: 查询类型
        """
        query_lower = query.lower()

        # Adaptive-RAG: 简单查询优先判定
        # 短查询且包含事实关键词，无需复杂检索策略
        if len(query) <= self.SIMPLE_MAX_LENGTH:
            for kw in self.FACTUAL_KEYWORDS:
                if kw in query_lower:
                    return QueryType.SIMPLE

        # 检查各类关键词
        for kw in self.FACTUAL_KEYWORDS:
            if kw in query_lower:
                return QueryType.FACTUAL

        for kw in self.EXPLANATORY_KEYWORDS:
            if kw in query_lower:
                return QueryType.EXPLANATORY

        for kw in self.COMPARATIVE_KEYWORDS:
            if kw in query_lower:
                return QueryType.COMPARATIVE

        for kw in self.PROCEDURAL_KEYWORDS:
            if kw in query_lower:
                return QueryType.PROCEDURAL

        # 默认为探索性查询
        return QueryType.EXPLORATORY

    def create_plan(
        self,
        query: str,
        user_id: str,
        space_id: str = None,
        retrieval_round: int = 0,
    ) -> RetrievalPlan:
        """
        创建检索计划。

        :param query: 用户查询
        :param user_id: 用户 ID
        :param space_id: 空间 ID
        :param retrieval_round: 当前检索轮次
        :return: 检索计划
        """
        query_type = self.classify_query(query)

        # 根据查询类型调整参数
        if query_type == QueryType.SIMPLE:
            # Adaptive-RAG: 简单查询走轻量路径，降低延迟和成本
            scope = "all"
            top_k = 3
            use_hyde = False
            use_rerank = False
        elif query_type == QueryType.FACTUAL:
            scope = "all"
            top_k = 5
            use_hyde = True
            use_rerank = True
        elif query_type == QueryType.EXPLANATORY:
            scope = "all"
            top_k = 8
            use_hyde = True
            use_rerank = True
        elif query_type == QueryType.COMPARATIVE:
            scope = "all"
            top_k = 10
            use_hyde = True
            use_rerank = True
        elif query_type == QueryType.PROCEDURAL:
            scope = "knowledge"
            top_k = 5
            use_hyde = False
            use_rerank = True
        else:
            scope = "all"
            top_k = 8
            use_hyde = True
            use_rerank = True

        # 如果指定了空间，覆盖 scope
        if space_id:
            scope = f"space:{space_id}"

        # 第二轮检索：CRAG 纠错策略
        if retrieval_round > 0:
            # CRAG: 扩大检索范围 + 适当增加 top_k（而非默认降低）
            if scope == "knowledge":
                scope = "all"  # 从知识库扩大到知识库+笔记
            top_k = max(5, top_k + 3)  # 扩大召回，而非缩减
            logger.info(f"【CRAG】第二轮检索扩大范围: scope={scope}, top_k={top_k}")

        plan = RetrievalPlan(
            query_type=query_type,
            scope=scope,
            top_k=top_k,
            use_hyde=use_hyde,
            use_rerank=use_rerank,
        )

        logger.info(f"【检索计划】query_type={query_type.value}, scope={scope}, top_k={top_k}")
        return plan

    def rewrite_query(self, original_query: str, failed_reason: str = None) -> str:
        """
        改写查询（当证据不足时）。

        策略：
        - 移除过于具体的限定词
        - 扩展同义词
        - 简化复杂查询

        :param original_query: 原始查询
        :param failed_reason: 失败原因
        :return: 改写后的查询
        """
        # 简单的改写策略
        rewritten = original_query

        # 移除引号
        rewritten = rewritten.replace('"', '').replace('"', '').replace('"', '')

        # 如果查询太长，取核心部分
        if len(rewritten) > 50:
            # 取前50个字符
            rewritten = rewritten[:50]

        logger.info(f"【查询改写】原始: {original_query} -> 改写: {rewritten}")
        return rewritten


# 全局实例
planner = Planner()
