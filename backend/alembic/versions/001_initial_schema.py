"""initial schema from SQLAlchemy models

Revision ID: 001_initial
Revises:
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_sessions_id"), "chat_sessions", ["id"], unique=False)
    op.create_index(op.f("ix_chat_sessions_user_id"), "chat_sessions", ["user_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_messages_id"), "chat_messages", ["id"], unique=False)

    op.create_table(
        "notes",
        sa.Column("id", sa.String(length=36), nullable=False, comment="UUID"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户ID"),
        sa.Column("title", sa.String(length=200), nullable=False, comment="笔记标题"),
        sa.Column("content", sa.Text(), nullable=False, comment="Markdown原文"),
        sa.Column("tags", sa.JSON(), nullable=True, comment='标签列表 ["AI", "FastAPI"]'),
        sa.Column("category", sa.String(length=50), nullable=True, comment="分类 work/study/life/project"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notes_user_id"), "notes", ["user_id"], unique=False)
    op.create_index("ix_notes_user_updated", "notes", ["user_id", "updated_at"], unique=False)

    op.create_table(
        "review_records",
        sa.Column("id", sa.String(length=36), nullable=False, comment="UUID"),
        sa.Column("note_id", sa.String(length=36), nullable=False, comment="笔记ID"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户ID"),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="上次回顾时间"),
        sa.Column("review_count", sa.Integer(), nullable=True, comment="回顾次数"),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True, comment="下次回顾时间"),
        sa.Column("interval_days", sa.Integer(), nullable=True, comment="当前间隔天数"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="创建时间"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", name="uq_review_record_note_id"),
    )
    op.create_index(op.f("ix_review_records_user_id"), "review_records", ["user_id"], unique=False)

    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False, comment="组织名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="组织描述"),
        sa.Column("owner_id", sa.String(length=36), nullable=False, comment="创建者 user_id"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_owner", "organizations", ["owner_id"], unique=False)

    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="成员 user_id (JWT UUID)"),
        sa.Column("username", sa.String(length=150), nullable=True, comment="成员用户名"),
        sa.Column("role", sa.String(length=20), nullable=True, comment="角色: owner/admin/member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_member_user", "organization_members", ["user_id"], unique=False)
    op.create_index("ix_org_member_org", "organization_members", ["org_id"], unique=False)

    op.create_table(
        "spaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False, comment="空间名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="空间描述"),
        sa.Column("created_by", sa.String(length=36), nullable=False, comment="创建者 user_id"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_space_org", "spaces", ["org_id"], unique=False)

    op.create_table(
        "space_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("space_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False, comment="资源类型: note/knowledge"),
        sa.Column("resource_id", sa.String(length=100), nullable=False, comment="资源 ID，note 使用 notes.id"),
        sa.Column("added_by", sa.String(length=36), nullable=False, comment="加入空间的用户 ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "resource_type", "resource_id", name="uq_space_document_resource"),
    )
    op.create_index("ix_space_document_space", "space_documents", ["space_id"], unique=False)
    op.create_index("ix_space_document_resource", "space_documents", ["resource_type", "resource_id"], unique=False)
    op.create_index("ix_space_document_added_by", "space_documents", ["added_by"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=True, comment="关联组织ID，可为空"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="操作者 user_id"),
        sa.Column("action", sa.String(length=50), nullable=False, comment="操作类型: create/update/delete/login/upload"),
        sa.Column("resource_type", sa.String(length=50), nullable=True, comment="资源类型: note/knowledge/space/member/org"),
        sa.Column("resource_id", sa.String(length=36), nullable=True, comment="资源ID"),
        sa.Column("detail", sa.JSON(), nullable=True, comment="操作详情"),
        sa.Column("ip_address", sa.String(length=45), nullable=True, comment="客户端IP"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_org", "audit_logs", ["org_id"], unique=False)
    op.create_index("ix_audit_user", "audit_logs", ["user_id"], unique=False)
    op.create_index("ix_audit_action", "audit_logs", ["action"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_action", table_name="audit_logs")
    op.drop_index("ix_audit_user", table_name="audit_logs")
    op.drop_index("ix_audit_org", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_space_document_added_by", table_name="space_documents")
    op.drop_index("ix_space_document_resource", table_name="space_documents")
    op.drop_index("ix_space_document_space", table_name="space_documents")
    op.drop_table("space_documents")

    op.drop_index("ix_space_org", table_name="spaces")
    op.drop_table("spaces")

    op.drop_index("ix_org_member_org", table_name="organization_members")
    op.drop_index("ix_org_member_user", table_name="organization_members")
    op.drop_table("organization_members")

    op.drop_index("ix_org_owner", table_name="organizations")
    op.drop_table("organizations")

    op.drop_index(op.f("ix_review_records_user_id"), table_name="review_records")
    op.drop_table("review_records")

    op.drop_index("ix_notes_user_updated", table_name="notes")
    op.drop_index(op.f("ix_notes_user_id"), table_name="notes")
    op.drop_table("notes")

    op.drop_index(op.f("ix_chat_messages_id"), table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index(op.f("ix_chat_sessions_user_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_id"), table_name="chat_sessions")
    op.drop_table("chat_sessions")
