"""
文档索引执行清单（K1）——写入者生命周期的持久状态。

每次领取（claim）在同一事务登记一条 writing 执行；发布成功在发布事务内
关闭（closed）；确定性失败（未写向量或写入确定失败）关闭为 closed；
异常路径结果不确定（远端可能已写入）保持 unknown，由清理任务的
"写入者已关闭"门槛阻止最终完成，直到协调/人工核验后关闭。

清理任务的 done 条件依赖本表：存在 writing/unknown 执行时禁止 done。
"""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import Column, String, DateTime, Integer, UniqueConstraint, func, Index

from app.models.chat_history import Base


class ExecutionStatus(str, PyEnum):
    WRITING = "writing"    # 执行中（可能仍向远端写入）
    CLOSED = "closed"      # 已确定关闭（成功发布 / 确定性失败）
    UNKNOWN = "unknown"    # 结果不确定（异常/取消/崩溃），待核验


class DocumentIndexExecution(Base):
    """索引执行清单：同文档代次唯一"""
    __tablename__ = "document_index_executions"
    __table_args__ = (
        UniqueConstraint("document_id", "generation", name="uq_execution_doc_gen"),
        Index("ix_execution_status", "status"),
    )

    execution_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), nullable=False, index=True, comment="文档ID")
    user_id = Column(String(36), nullable=False, comment="所属用户ID")
    generation = Column(Integer, nullable=False, comment="执行代次")
    token = Column(String(64), nullable=False, comment="执行令牌（与领取一致）")
    status = Column(String(20), nullable=False, default=ExecutionStatus.WRITING,
                    comment="writing / closed / unknown")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True, comment="关闭时间")
    lease_expires_at = Column(DateTime(timezone=True), nullable=True,
                              comment="执行租约到期（失联判定依据；不证明远端停止）")
    close_reason = Column(String(200), nullable=True, comment="关闭依据/错误码")
