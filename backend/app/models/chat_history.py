"""
对话历史模型 —— 存储用户与 AI 的对话记录。

数据表：
- chat_sessions: 会话表（一个会话包含多条消息）
- chat_messages: 消息表（每条消息属于一个会话）

关系：ChatSession 1 -> N ChatMessage（级联删除）
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class ChatSession(Base):  # type: ignore[misc,valid-type]  # 同上
    """会话表 —— 记录用户的一次对话"""
    __tablename__ = "chat_sessions"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), index=True, nullable=False)  # 关联用户（逻辑外键）
    title = Column(String(255), default="新的对话")
    metadata_ = Column(JSON, name="metadata")  # metadata 是 SQL 保留字，加下划线
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 关系：一个会话包含多条消息
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):  # type: ignore[misc,valid-type]  # 同上
    """消息表 —— 记录单条对话消息"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), ForeignKey("chat_sessions.id"))
    role = Column(String(32), nullable=False)      # "user" 或 "assistant"
    content = Column(Text, nullable=False)           # 消息内容
    metadata_ = Column(JSON, name="metadata")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 关系：多条消息属于一个会话
    session = relationship("ChatSession", back_populates="messages")