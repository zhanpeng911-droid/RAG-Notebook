"""add recovery outcome fields to document_index_executions (A batch)

Revision ID: 014_execution_recovery
Revises: 013_execution_lease
Create Date: 2026-09-08

结构化恢复事实：recovery_outcome（recovered / needs_reupload）+ recovered_at。
恢复凭据从 error_message 子串迁移到结构化字段；重放/确认读取结构化事实，
错误文案仅展示给人，不再是业务凭据。closed_at 仅用于确定关闭；unknown
写回不设置关闭时间（语义修正）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014_execution_recovery"
down_revision: Union[str, Sequence[str], None] = "013_execution_lease"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_index_executions",
        sa.Column("recovery_outcome", sa.String(length=30), nullable=True,
                  comment="结构化恢复结果：recovered / needs_reupload"),
    )
    op.add_column(
        "document_index_executions",
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True,
                  comment="实际恢复时间（重放不更新）"),
    )


def downgrade() -> None:
    op.drop_column("document_index_executions", "recovered_at")
    op.drop_column("document_index_executions", "recovery_outcome")
