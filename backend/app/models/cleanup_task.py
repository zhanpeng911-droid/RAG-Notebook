"""
文档清理任务模型（C4）——持久、公平、可证明的向量清理队列。

发布旧代次回收与文档删除后的残余清理都登记为本任务：
- 删除文档后 SQL 行已不存在，任务是对抗"迟到写入"的唯一持久依据；
- 发布提交成功后才创建任务（与发布同事务），提交失败不产生清理工作；
- worker 公平领取（next_retry_at, created_at, id），条件写 claim_token +
  租约，清理在事务外执行；失败指数退避，超限进入 blocked 人工可见。
"""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import Column, String, DateTime, Integer, Text, UniqueConstraint, func, Index

from app.models.chat_history import Base


class CleanupTaskStatus(str, PyEnum):
    PENDING = "pending"      # 待领取
    CLAIMED = "claimed"      # 已领取（租约内）
    DONE = "done"            # 已核验清理完成
    FAILED = "failed"        # 待重试（指数退避）
    BLOCKED = "blocked"      # 重试超限，需人工介入


class DocumentCleanupTask(Base):
    """文档向量清理任务表"""
    __tablename__ = "document_cleanup_tasks"
    __table_args__ = (
        # 清理目标唯一：同一文档同一代次同一原因只有一条任务
        UniqueConstraint("document_id", "generation", "reason", name="uq_cleanup_target"),
        Index("ix_cleanup_due", "status", "next_retry_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), nullable=False, index=True, comment="文档ID")
    user_id = Column(String(36), nullable=False, comment="所属用户ID")
    md5 = Column(String(32), nullable=False, comment="内容指纹（定位向量）")
    generation = Column(Integer, nullable=False, comment="任务关联代次")
    legacy_chunk_count = Column(Integer, nullable=False, default=0,
                                comment="旧版固定 ID 向量数量（发布时已知的 v1 前缀残留）")
    reason = Column(String(40), nullable=False,
                    comment="superseded_generation=旧代次回收；document_deleted=删除后残余")
    status = Column(String(20), nullable=False, default=CleanupTaskStatus.PENDING,
                    comment="任务状态")
    attempt_count = Column(Integer, nullable=False, default=0, comment="已尝试次数")
    next_retry_at = Column(DateTime(timezone=True), server_default=func.now(),
                           comment="最早可领取时间（失败退避）")
    claim_token = Column(String(64), nullable=True, comment="当前领取令牌")
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, comment="领取租约到期")
    last_error = Column(Text, nullable=True, comment="最后一次失败原因（不含敏感正文）")
    verified_empty_at = Column(DateTime(timezone=True), nullable=True,
                               comment="首次核验为空时间（宽限后二次核验通过才 done）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
