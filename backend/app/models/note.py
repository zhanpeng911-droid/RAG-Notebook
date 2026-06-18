"""
笔记模型 —— 存储用户的 Markdown 笔记。

字段说明：
- id: UUID 主键
- user_id: 用户ID（逻辑外键，不做物理约束）
- title: 笔记标题
- content: Markdown 原文
- tags: JSON 数组，如 ["AI", "FastAPI"]（由 LLM 自动生成）
- category: 分类（work/study/life/project，由 LLM 自动生成）
- created_at / updated_at: 时间戳

索引：
- ix_notes_user_updated: (user_id, updated_at) 联合索引，优化按用户+时间排序查询
"""
from sqlalchemy import Column, String, Text, DateTime, JSON, Index
from sqlalchemy.sql import func
from app.models.chat_history import Base


class Note(Base):
    """笔记表 —— 存储用户的 Markdown 笔记"""
    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_user_updated", "user_id", "updated_at"),
    )

    id = Column(String(36), primary_key=True, comment="UUID")
    user_id = Column(String(36), index=True, nullable=False, comment="用户ID")
    title = Column(String(200), nullable=False, comment="笔记标题")
    content = Column(Text, nullable=False, comment="Markdown原文")
    tags = Column(JSON, comment='标签列表 ["AI", "FastAPI"]')
    category = Column(String(50), comment="分类 work/study/life/project")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")
