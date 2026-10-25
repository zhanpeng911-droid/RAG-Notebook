"""知识洞察 API 与结构化输出的 Pydantic 契约。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    anchor_ids: list[str] = Field(default_factory=list)
    quote: Optional[str] = None


class SummaryClaim(EvidenceRef):
    id: Optional[str] = None
    text: str


class SummarySection(EvidenceRef):
    id: Optional[str] = None
    title: str
    summary: str = ""
    claims: list[SummaryClaim] = Field(default_factory=list)


class SummaryTerm(EvidenceRef):
    name: str
    description: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)


class SummaryRelation(EvidenceRef):
    source: str
    target: str
    relation_type: str = "related_to"
    description: Optional[str] = None


class SummaryPayload(BaseModel):
    """LLM 必须返回的结构；未知字段忽略，便于模型多输出版本兼容。"""

    overview: SummaryClaim
    key_points: list[SummaryClaim] = Field(default_factory=list)
    sections: list[SummarySection] = Field(default_factory=list)
    terms: list[SummaryTerm] = Field(default_factory=list)
    open_questions: list[SummaryClaim] = Field(default_factory=list)
    relations: list[SummaryRelation] = Field(default_factory=list)


class SectionBlockPayload(BaseModel):
    """长文档分层分析中单个片段的输出。"""

    sections: list[SummarySection] = Field(default_factory=list)
    terms: list[SummaryTerm] = Field(default_factory=list)


class InsightGenerateRequest(BaseModel):
    force: bool = False
    model: Optional[str] = None
    artifact_type: Literal["summary"] = "summary"


class InsightGenerateResponse(BaseModel):
    job_id: Optional[str] = None
    status: str
    already_generated: bool = False
    artifact_id: Optional[str] = None
    message: Optional[str] = None


class MindmapNode(BaseModel):
    id: str
    label: str
    anchor_ids: list[str] = Field(default_factory=list)
    children: list["MindmapNode"] = Field(default_factory=list)


class MindmapPayload(BaseModel):
    root: MindmapNode
    node_count: int = 0
    max_depth: int = 0
    schema_version: str = "1.0"


MindmapNode.model_rebuild()


class MindmapNodeEdit(BaseModel):
    node_id: str = Field(min_length=1, max_length=120)
    label: Optional[str] = Field(default=None, max_length=500)
    sort_order: Optional[int] = None


class MindmapEditRequest(BaseModel):
    expected_revision: Optional[str] = Field(default=None, max_length=64)
    edits: list[MindmapNodeEdit] = Field(default_factory=list)


class ConceptPatch(BaseModel):
    status: Literal["auto", "confirmed", "rejected"]


class RelationPatch(BaseModel):
    verification_state: Literal["auto", "confirmed", "rejected"]
