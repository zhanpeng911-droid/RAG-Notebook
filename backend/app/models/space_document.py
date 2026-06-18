"""
空间文档关联模型 —— 将笔记等用户内容加入组织空间。

通过 SpaceDocument 记录建立空间（Space）与资源（笔记、知识库文档等）
之间的多对多关联关系，支持按空间查询其包含的所有资源。
"""
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.sql import func
from app.models.chat_history import Base


class SpaceDocument(Base):
    """空间文档关联 ORM 模型。

    维护组织空间（Space）与用户资源（笔记、知识库文档）之间的关联关系。
    同一个资源不能被重复添加到同一空间中（由唯一约束保证）。

    表名: space_documents

    约束:
        uq_space_document_resource: (space_id, resource_type, resource_id) 唯一约束，
            防止同一资源被重复添加到同一空间。
        ix_space_document_space: 按空间 ID 查询的索引。
        ix_space_document_resource: 按资源类型和资源 ID 查询的索引。
        ix_space_document_added_by: 按操作用户查询的索引。

    属性:
        id: 关联记录唯一标识（UUID v4）。
        space_id: 所属空间的 ID，级联删除（空间删除时关联记录自动清除）。
        resource_type: 资源类型，"note" 表示笔记，"knowledge" 表示知识库文档。
        resource_id: 资源 ID，笔记对应 notes.id，知识库文档对应知识库的文档 ID。
        added_by: 将该资源加入空间的用户 ID。
        created_at: 关联创建时间，由数据库服务器自动填充。
    """
    __tablename__ = "space_documents"
    __table_args__ = (
        UniqueConstraint("space_id", "resource_type", "resource_id", name="uq_space_document_resource"),
        Index("ix_space_document_space", "space_id"),
        Index("ix_space_document_resource", "resource_type", "resource_id"),
        Index("ix_space_document_added_by", "added_by"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    space_id = Column(String(36), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    resource_type = Column(String(30), nullable=False, default="note", comment="资源类型: note/knowledge")
    resource_id = Column(String(100), nullable=False, comment="资源 ID，note 使用 notes.id")
    added_by = Column(String(36), nullable=False, comment="加入空间的用户 ID")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
