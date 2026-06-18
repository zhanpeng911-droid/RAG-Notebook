"""
空间模型 —— Space（组织下的知识库空间）
"""
import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.models.chat_history import Base


class Space(Base):
    __tablename__ = "spaces"
    __table_args__ = (
        Index("ix_space_org", "org_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False, comment="空间名称")
    description = Column(Text, default="", comment="空间描述")
    created_by = Column(String(36), nullable=False, comment="创建者 user_id")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
