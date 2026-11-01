"""笔记图谱模型 —— 显式内部链接、手动关联与导图用户修订。

设计边界（V2 任务书 §6/§8）：
- 一个 Note 对应一个图节点，节点身份是 note_id，不按标题合并；
- 正文中的 [[...]] 解析为 NoteLinkOccurrence，多个 occurrence 聚合成一条边；
- 手动关联独立存储，不伪装成正文引用，删除它不影响正文链接；
- 导图用户编辑与 AI 产物分开存储，重新生成不覆盖用户标题/顺序。
"""
from sqlalchemy import (
    Column, DateTime, Index, Integer, String, Text, UniqueConstraint, func,
)

from app.models.chat_history import Base


class NoteLinkStatus:
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    TARGET_DELETED = "target_deleted"


class NoteLinkOccurrence(Base):
    """正文中一处 [[...]] 链接的出现位置与解析结果。"""

    __tablename__ = "note_link_occurrences"
    __table_args__ = (
        # The composite owner+source/target indexes already cover owner-first
        # reads. Keeping a standalone owner index and an unused revision index
        # made a 100-link save pay five index writes per occurrence.
        Index("ix_note_occ_owner_source", "owner_user_id", "source_note_id"),
        Index("ix_note_occ_owner_target", "owner_user_id", "target_note_id"),
    )

    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), nullable=False)
    source_note_id = Column(String(36), nullable=False)
    source_revision = Column(String(64), nullable=False, comment="正文内容 SHA256")
    target_note_id = Column(String(36), nullable=True, comment="解析成功的目标笔记ID")
    raw_token = Column(String(500), nullable=False)
    display_text = Column(String(300), nullable=True)
    char_start = Column(Integer, nullable=False)
    char_end = Column(Integer, nullable=False)
    context_text = Column(Text, nullable=True, comment="所在段落片段")
    status = Column(String(20), nullable=False, default=NoteLinkStatus.UNRESOLVED)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NoteManualRelation(Base):
    """用户显式建立的手动关联（不修改正文）。"""

    __tablename__ = "note_manual_relations"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "source_note_id", "target_note_id",
                         "relation_type", name="uq_note_manual_triple"),
        Index("ix_note_manual_owner_source", "owner_user_id", "source_note_id"),
        Index("ix_note_manual_owner_target", "owner_user_id", "target_note_id"),
    )

    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), nullable=False, index=True)
    source_note_id = Column(String(36), nullable=False)
    target_note_id = Column(String(36), nullable=False)
    relation_type = Column(String(30), nullable=False, default="manual",
                           server_default="manual")
    reason = Column(Text, nullable=True)
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())


class KnowledgeMindmapEdit(Base):
    """用户对导图节点标题/同级顺序的修订（与 AI 产物分层）。"""

    __tablename__ = "knowledge_mindmap_edits"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "source_type", "source_id", "node_id",
                         name="uq_mindmap_edit_node"),
        Index("ix_mindmap_edit_owner_source", "owner_user_id", "source_type", "source_id"),
    )

    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), nullable=False, index=True)
    source_type = Column(String(20), nullable=False)
    source_id = Column(String(36), nullable=False)
    base_source_version_id = Column(String(36), nullable=True)
    node_id = Column(String(120), nullable=False)
    label = Column(String(500), nullable=True)
    sort_order = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
