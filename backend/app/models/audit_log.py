"""
审计日志模型 —— AuditLog（只增不删不更新）。

审计日志采用追加写入策略，一旦创建便不可修改或删除，
以确保操作记录的完整性和不可篡改性，满足合规审计需求。
"""
import uuid
from sqlalchemy import Column, String, DateTime, JSON, Index
from sqlalchemy.sql import func
from app.models.chat_history import Base


class AuditLog(Base):
    """审计日志 ORM 模型。

    记录系统中所有关键操作的审计信息，包括用户登录、资源创建/修改/删除、
    文件上传等。采用只增不删不更新策略，保证日志的完整性和可追溯性。

    表名: audit_logs

    索引:
        ix_audit_org: 按组织 ID 查询的日志索引。
        ix_audit_user: 按用户 ID 查询的日志索引。
        ix_audit_action: 按操作类型查询的日志索引。

    属性:
        id: 日志唯一标识（UUID v4）。
        org_id: 关联的组织 ID，可为空（系统级操作时无组织归属）。
        user_id: 执行操作的用户 ID。
        action: 操作类型，如 create/update/delete/login/upload。
        resource_type: 操作的资源类型，如 note/knowledge/space/member/org。
        resource_id: 被操作资源的 ID。
        detail: 操作详情，以 JSON 格式存储额外的上下文信息。
        ip_address: 客户端 IP 地址，支持 IPv4 和 IPv6（最大 45 字符）。
        created_at: 操作发生时间，由数据库服务器自动填充。
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_org", "org_id"),
        Index("ix_audit_user", "user_id"),
        Index("ix_audit_action", "action"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String(36), nullable=True, comment="关联组织ID，可为空")
    user_id = Column(String(36), nullable=False, comment="操作者 user_id")
    action = Column(String(50), nullable=False, comment="操作类型: create/update/delete/login/upload")
    resource_type = Column(String(50), nullable=True, comment="资源类型: note/knowledge/space/member/org")
    resource_id = Column(String(36), nullable=True, comment="资源ID")
    detail = Column(JSON, nullable=True, comment="操作详情")
    ip_address = Column(String(45), nullable=True, comment="客户端IP")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
