"""
回顾记录模型 —— 存储笔记的间隔重复回顾信息。

艾宾浩斯遗忘曲线间隔：[1, 2, 4, 7, 15, 30] 天

字段说明：
- note_id: 关联笔记（物理外键，级联删除）
- review_count: 已回顾次数
- next_review_at: 下次回顾时间
- interval_days: 当前间隔天数
"""
from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from app.models.chat_history import Base


class ReviewRecord(Base):
    """回顾记录表 —— 管理笔记的间隔重复回顾"""
    __tablename__ = "review_records"
    __table_args__ = (
        UniqueConstraint("note_id", name="uq_review_record_note_id"),
    )

    id = Column(String(36), primary_key=True, comment="UUID")
    note_id = Column(String(36), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, comment="笔记ID")
    user_id = Column(String(36), index=True, nullable=False, comment="用户ID")
    last_reviewed_at = Column(DateTime(timezone=True), comment="上次回顾时间")
    review_count = Column(Integer, default=0, comment="回顾次数")
    next_review_at = Column(DateTime(timezone=True), comment="下次回顾时间")
    interval_days = Column(Integer, default=1, comment="当前间隔天数")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
