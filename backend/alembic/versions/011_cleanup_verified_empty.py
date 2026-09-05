"""add verified_empty_at to document_cleanup_tasks (C4 two-pass verify)

Revision ID: 011_cleanup_verified_empty
Revises: 010_cleanup_tasks
Create Date: 2026-09-05

一次查空不能证明清理完成（旧 worker 可能在核验后迟到写入）：
首次核验为空只记录 verified_empty_at 并安排宽限窗口后复核，
连续两次核验为空且间隔达到宽限期才允许 done。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011_cleanup_verified_empty"
down_revision: Union[str, Sequence[str], None] = "010_cleanup_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_cleanup_tasks",
        sa.Column("verified_empty_at", sa.DateTime(timezone=True), nullable=True,
                  comment="首次核验为空时间（宽限后二次核验通过才 done）"),
    )


def downgrade() -> None:
    op.drop_column("document_cleanup_tasks", "verified_empty_at")
