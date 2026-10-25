"""
知识工作台模型 —— 可验证来源、结构化知识产物与知识图谱。

设计原则（对应 N0-N3）：
- 来源版本（KnowledgeSourceVersion）以 owner + 资源身份 + 代次定位，
  不复用 MD5 作为授权身份；
- 原文锚点（SourceAnchor）只记录解析器真实提供的位置信息：
  没有页码的格式 page_no 必须为空，不伪造页码；
- 知识产物（KnowledgeArtifact）与证据（KnowledgeEvidence）分离，
  对外展示的每条事实性结论都必须能回指到锚点；
- 概念/关系（KnowledgeConcept/KnowledgeRelation）按 owner + space 归一，
  关系证据挂在证据表上，来源失效后边自然隐藏而不是删除审计。
"""
from sqlalchemy import (
    Column, DateTime, Float, Integer, JSON, String, Text,
    UniqueConstraint, Index, func,
)

from app.models.chat_history import Base


class SourceVersionStatus:
    PENDING = "pending"
    ACTIVE = "active"
    STALE = "stale"
    INVALID = "invalid"


class SourceType:
    DOCUMENT = "document"
    NOTE = "note"


class ArtifactType:
    SUMMARY = "summary"
    MINDMAP = "mindmap"


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


class EvidenceStatus:
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"
    STALE = "stale"


class KnowledgeSourceVersion(Base):
    """一份源资源在某个代次/内容版本下的快照身份。"""

    __tablename__ = "knowledge_source_versions"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "generation",
                         name="uq_ksv_source_gen"),
        Index("ix_ksv_owner_source", "owner_user_id", "source_type", "source_id"),
        Index("ix_ksv_owner_status", "owner_user_id", "status"),
    )

    id = Column(String(36), primary_key=True, comment="来源版本ID（UUID）")
    owner_user_id = Column(String(36), nullable=False, index=True, comment="所有者用户ID")
    space_id = Column(String(36), nullable=True, index=True, comment="所属空间ID")
    source_type = Column(String(20), nullable=False, comment="document/note")
    source_id = Column(String(36), nullable=False, comment="文档ID或笔记ID")
    generation = Column(Integer, nullable=False, default=0, server_default="0",
                        comment="文档索引代次；笔记为递增版本号")
    content_hash = Column(String(64), nullable=True, comment="源内容指纹（文件MD5或笔记SHA256）")
    parser_version = Column(String(32), nullable=False, default="anchor-v1",
                            server_default="anchor-v1", comment="解析/锚点协议版本")
    status = Column(String(20), nullable=False, default=SourceVersionStatus.PENDING,
                    server_default=SourceVersionStatus.PENDING,
                    comment="pending/active/stale/invalid")
    chunk_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceAnchor(Base):
    """可核验的原文锚点：分块位置、引文与定位方式。"""

    __tablename__ = "source_anchors"
    __table_args__ = (
        UniqueConstraint("source_version_id", "chunk_index",
                         name="uq_anchor_version_chunk"),
        Index("ix_anchor_owner_source_gen", "owner_user_id", "source_id", "generation"),
        Index("ix_anchor_version", "source_version_id"),
    )

    id = Column(String(36), primary_key=True, comment="锚点ID（UUID）")
    source_version_id = Column(String(36), nullable=False, comment="来源版本ID")
    owner_user_id = Column(String(36), nullable=False, comment="所有者用户ID（冗余授权字段）")
    space_id = Column(String(36), nullable=True, comment="所属空间ID")
    source_type = Column(String(20), nullable=False, comment="document/note")
    source_id = Column(String(36), nullable=False, comment="文档ID或笔记ID")
    generation = Column(Integer, nullable=False, default=0, server_default="0")
    chunk_id = Column(String(200), nullable=True, comment="向量库分块ID（无向量时为 null）")
    chunk_index = Column(Integer, nullable=False, comment="分块序号（从0开始）")
    page_no = Column(Integer, nullable=True, comment="真实页码（1起）；解析器未提供则为空")
    heading_path = Column(String(1000), nullable=True, comment="章节路径，如 第一章 > 1.2")
    char_start = Column(Integer, nullable=True, comment="在来源文本中的起始字符偏移")
    char_end = Column(Integer, nullable=True, comment="在来源文本中的结束字符偏移")
    quote_text = Column(Text, nullable=False, comment="该锚点对应的真实原文片段")
    quote_hash = Column(String(64), nullable=False, comment="规范化引文的 SHA256")
    locator_kind = Column(String(20), nullable=False, default="chunk",
                          comment="page/char/section/chunk")
    extraction_confidence = Column(Float, nullable=False, default=1.0, server_default="1.0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KnowledgeGenerationJob(Base):
    """知识生成任务（与文档索引状态严格分离）。"""

    __tablename__ = "knowledge_generation_jobs"
    __table_args__ = (
        UniqueConstraint("source_version_id", "artifact_type",
                         name="uq_insight_job_version_type"),
        Index("ix_insight_job_owner", "owner_user_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, comment="任务ID（UUID）")
    owner_user_id = Column(String(36), nullable=False, index=True)
    source_version_id = Column(String(36), nullable=False, comment="来源版本ID")
    source_type = Column(String(20), nullable=False, default=SourceType.DOCUMENT)
    source_id = Column(String(36), nullable=False, comment="文档ID或笔记ID")
    artifact_type = Column(String(20), nullable=False, default=ArtifactType.SUMMARY)
    status = Column(String(20), nullable=False, default=JobStatus.QUEUED,
                    server_default=JobStatus.QUEUED,
                    comment="queued/running/succeeded/failed/stale")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    prompt_version = Column(String(32), nullable=False, default="insight-v1")
    model_id = Column(String(120), nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KnowledgeArtifact(Base):
    """结构化知识产物（overview/sections/key_points/terms/mindmap）。"""

    __tablename__ = "knowledge_artifacts"
    __table_args__ = (
        UniqueConstraint("source_version_id", "artifact_type",
                         name="uq_artifact_version_type"),
        Index("ix_artifact_owner_source", "owner_user_id", "source_id"),
    )

    id = Column(String(36), primary_key=True, comment="产物ID（UUID）")
    owner_user_id = Column(String(36), nullable=False, index=True)
    source_version_id = Column(String(36), nullable=False)
    source_type = Column(String(20), nullable=False, default=SourceType.DOCUMENT)
    source_id = Column(String(36), nullable=False)
    generation = Column(Integer, nullable=False, default=0, server_default="0")
    artifact_type = Column(String(20), nullable=False, default=ArtifactType.SUMMARY)
    schema_version = Column(String(20), nullable=False, default="1.0")
    payload_json = Column(JSON, nullable=False, comment="结构化 payload")
    status = Column(String(20), nullable=False, default="active", server_default="active",
                    comment="active/stale")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KnowledgeEvidence(Base):
    """结论/节点/关系 → 原文锚点 的证据链。"""

    __tablename__ = "knowledge_evidence"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "target_kind", "target_id", "anchor_id",
                         name="uq_evidence_target_anchor"),
        Index("ix_evidence_artifact", "artifact_id"),
        Index("ix_evidence_anchor", "anchor_id"),
        Index("ix_evidence_source_version", "source_version_id"),
    )

    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), nullable=False, index=True)
    artifact_id = Column(String(36), nullable=True, comment="产物ID；概念/关系证据可为空")
    source_version_id = Column(String(36), nullable=False)
    source_type = Column(String(20), nullable=False, default=SourceType.DOCUMENT)
    source_id = Column(String(36), nullable=False)
    target_kind = Column(String(30), nullable=False,
                         comment="summary/claim/key_point/section/term/mindmap_node/concept/relation")
    target_id = Column(String(120), nullable=False, comment="产物内部稳定ID")
    anchor_id = Column(String(36), nullable=False)
    supporting_quote = Column(Text, nullable=True, comment="模型引用的原文片段")
    quote_hash = Column(String(64), nullable=True)
    verification_status = Column(String(20), nullable=False,
                                 default=EvidenceStatus.VERIFIED,
                                 comment="verified/unverified/rejected/stale")
    confidence = Column(Float, nullable=False, default=1.0, server_default="1.0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KnowledgeConcept(Base):
    """按 owner + space 归一的概念节点。"""

    __tablename__ = "knowledge_concepts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "space_key", "normalized_name",
                         name="uq_concept_owner_space_name"),
        Index("ix_concept_owner_space", "owner_user_id", "space_key"),
    )

    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), nullable=False, index=True)
    space_id = Column(String(36), nullable=True)
    space_key = Column(String(36), nullable=False, default="", server_default="",
                       comment="空间归一键（空串表示个人知识库）")
    canonical_name = Column(String(200), nullable=False)
    normalized_name = Column(String(200), nullable=False)
    aliases_json = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="auto", server_default="auto",
                    comment="auto/confirmed/rejected")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KnowledgeRelation(Base):
    """概念关系（少量明确语义；证据由 KnowledgeEvidence 承载）。"""

    __tablename__ = "knowledge_relations"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "space_key", "source_concept_id",
                         "target_concept_id", "relation_type",
                         name="uq_relation_owner_space_triple"),
        Index("ix_relation_owner_space", "owner_user_id", "space_key"),
    )

    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), nullable=False, index=True)
    space_id = Column(String(36), nullable=True)
    space_key = Column(String(36), nullable=False, default="", server_default="")
    source_concept_id = Column(String(36), nullable=False)
    target_concept_id = Column(String(36), nullable=False)
    relation_type = Column(String(30), nullable=False,
                           comment="is_a/part_of/depends_on/related_to/contrasts_with")
    description = Column(Text, nullable=True)
    verification_state = Column(String(20), nullable=False, default="auto",
                                server_default="auto", comment="auto/confirmed/rejected/unverified")
    confidence = Column(Float, nullable=False, default=1.0, server_default="1.0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
