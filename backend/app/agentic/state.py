"""
Agent 状态定义 —— Agentic RAG 的核心状态模型。

状态在 Agent 执行过程中流转，记录检索轮次、证据、答案等信息。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

from app.rag.retrieval_service import Evidence


class AgentPhase(str, Enum):
    """Agent 执行阶段"""
    STARTED = "started"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    GRADING_EVIDENCE = "grading_evidence"
    REWRITING_QUERY = "rewriting_query"
    GENERATING_ANSWER = "generating_answer"
    CITATION = "citation"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class AgentState:
    """
    Agent 执行状态 —— 在整个 Agent 生命周期中流转。

    属性:
        user_id: 用户 ID
        session_id: 会话 ID（可选）
        query: 用户原始查询
        rewritten_queries: 改写后的查询列表
        current_retrieval_round: 当前检索轮次
        max_retrieval_rounds: 最大检索轮次
        evidences: 检索到的证据列表
        answer: 生成的答案
        citations: 引用列表
        phase: 当前执行阶段
        error: 错误信息
        metadata: 额外元数据
    """
    user_id: str
    query: str
    session_id: Optional[str] = None
    space_id: Optional[str] = None
    rewritten_queries: List[str] = field(default_factory=list)
    current_retrieval_round: int = 0
    max_retrieval_rounds: int = 2
    evidences: List[Evidence] = field(default_factory=list)
    answer: Optional[str] = None
    citations: List[Dict[str, Any]] = field(default_factory=list)
    phase: AgentPhase = AgentPhase.STARTED
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_evidence(self, evidence: Evidence):
        """添加证据"""
        self.evidences.append(evidence)

    def add_citation(self, citation: Dict[str, Any]):
        """添加引用"""
        self.citations.append(citation)

    def increment_round(self):
        """增加检索轮次"""
        self.current_retrieval_round += 1

    def can_retry(self) -> bool:
        """是否可以重试"""
        return self.current_retrieval_round < self.max_retrieval_rounds

    def to_dict(self) -> dict:
        """转换为字典（用于 SSE 事件）"""
        return {
            "user_id": self.user_id,
            "query": self.query,
            "phase": self.phase.value,
            "retrieval_round": self.current_retrieval_round,
            "evidence_count": len(self.evidences),
            "citation_count": len(self.citations),
            "has_answer": self.answer is not None,
            "error": self.error,
        }
