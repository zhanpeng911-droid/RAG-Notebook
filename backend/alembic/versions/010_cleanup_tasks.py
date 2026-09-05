"""add document_cleanup_tasks table for C4 persistent cleanup

Revision ID: 010_cleanup_tasks
Revises: 009_agent_run_heartbeat
Create Date: 2026-09-05

持久清理队列：发布旧代次回收与删除后残余清理的唯一依据。
新增表，无数据迁移；downgrade 删表即回退（任务可由巡检重建，无破坏性）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010_cleanup_tasks"
down_revision: Union[str, Sequence[str], None] = "009_agent_run_heartbeat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_cleanup_tasks",
        sa.Column("id", sa.String(length=36), nullable=False, comment="任务ID"),
        sa.Column("document_id", sa.String(length=36), nullable=False, comment="文档ID"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="所属用户ID"),
        sa.Column("md5", sa.String(length=32), nullable=False, comment="内容指纹"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="任务关联代次"),
        sa.Column("legacy_chunk_count", sa.Integer(), nullable=False,
                  server_default="0", comment="旧版固定 ID 向量数量"),
        sa.Column("reason", sa.String(length=40), nullable=False,
                  comment="superseded_generation / document_deleted"),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="pending", comment="任务状态"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0",
                  comment="已尝试次数"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  comment="最早可领取时间"),
        sa.Column("claim_token", sa.String(length=64), nullable=True, comment="领取令牌"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True,
                  comment="领取租约到期"),
        sa.Column("last_error", sa.Text(), nullable=True, comment="最后失败原因"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "generation", "reason", name="uq_cleanup_target"),
    )
    op.create_index("ix_cleanup_due", "document_cleanup_tasks",
                    ["status", "next_retry_at"])
    op.create_index("ix_document_cleanup_tasks_document_id", "document_cleanup_tasks",
                    ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_cleanup_tasks_document_id", table_name="document_cleanup_tasks")
    op.drop_index("ix_cleanup_due", table_name="document_cleanup_tasks")
    op.drop_table("document_cleanup_tasks")
