"""
组织与成员模型 —— Organization, OrganizationMember
"""
import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.models.chat_history import Base


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        Index("ix_org_owner", "owner_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False, comment="组织名称")
    description = Column(Text, default="", comment="组织描述")
    owner_id = Column(String(36), nullable=False, comment="创建者 user_id")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        Index("ix_org_member_user", "user_id"),
        Index("ix_org_member_org", "org_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), nullable=False, comment="成员 user_id (JWT UUID)")
    username = Column(String(150), nullable=True, comment="成员用户名")
    role = Column(String(20), default="member", comment="角色: owner/admin/member")
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
