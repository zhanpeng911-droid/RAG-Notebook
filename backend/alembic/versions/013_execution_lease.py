"""add lease_expires_at to document_index_executions for P1 crash recovery

Revision ID: 013_execution_lease
Revises: 012_index_executions
Create Date: 2026-09-06

写执行登记时设置租约；协调任务将租约过期的 writing 执行列为失联
（unknown），不证明远端停止——关联清理任务在执行关闭前不得 done。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013_execution_lease"
down_revision: Union[str, Sequence[str], None] = "012_index_executions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_index_executions",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True,
                  comment="执行租约到期（失联判定依据；不证明远端停止）"),
    )


def downgrade() -> None:
    op.drop_column("document_index_executions", "lease_expires_at")
