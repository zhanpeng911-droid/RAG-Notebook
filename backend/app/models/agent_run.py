"""
Agent 运行记录模型 —— 记录 Agent 的执行过程和结果。

数据表：
- agent_runs: Agent 运行记录
- agent_steps: Agent 执行步骤
- agent_feedback: 用户反馈
"""
from enum import Enum as PyEnum

from sqlalchemy import Column, String, DateTime, Text, Integer, JSON, func
from app.models.chat_history import Base


class AgentRunStatus(str, PyEnum):
    """Agent 运行状态"""
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class AgentRun(Base):
    """Agent 运行记录表"""
    __tablename__ = "agent_runs"

    id = Column(String(36), primary_key=True, comment="运行ID（UUID）")
    user_id = Column(String(36), nullable=False, index=True, comment="用户ID")
    session_id = Column(String(64), nullable=True, index=True, comment="会话ID")
    space_id = Column(String(36), nullable=True, comment="空间ID")

    query = Column(Text, nullable=False, comment="用户查询")
    query_type = Column(String(30), nullable=True, comment="查询类型")
    answer = Column(Text, nullable=True, comment="生成的答案")

    status = Column(String(20), nullable=False, default=AgentRunStatus.STARTED, comment="运行状态")
    error_message = Column(Text, nullable=True, comment="错误信息")

    retrieval_rounds = Column(Integer, default=0, comment="检索轮次")
    evidence_count = Column(Integer, default=0, comment="证据数量")
    citation_count = Column(Integer, default=0, comment="引用数量")

    total_time_ms = Column(Integer, nullable=True, comment="总耗时（毫秒）")
    model_config = Column(JSON, nullable=True, comment="模型配置")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    completed_at = Column(DateTime(timezone=True), nullable=True, comment="完成时间")


class AgentStep(Base):
    """Agent 执行步骤表"""
    __tablename__ = "agent_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), nullable=False, index=True, comment="运行ID")
    user_id = Column(String(36), nullable=False, index=True, comment="用户ID")

    phase = Column(String(30), nullable=False, comment="执行阶段")
    step_data = Column(JSON, nullable=True, comment="步骤数据")

    duration_ms = Column(Integer, nullable=True, comment="耗时（毫秒）")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")


class AgentFeedback(Base):
    """用户反馈表"""
    __tablename__ = "agent_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), nullable=False, index=True, comment="运行ID")
    user_id = Column(String(36), nullable=False, index=True, comment="用户ID")

    rating = Column(Integer, nullable=False, comment="评分（1-5）")
    comment = Column(Text, nullable=True, comment="评论")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
