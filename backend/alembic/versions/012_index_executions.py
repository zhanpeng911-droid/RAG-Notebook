"""add document_index_executions table for K1 writer close protocol

Revision ID: 012_index_executions
Revises: 011_cleanup_verified_empty
Create Date: 2026-09-05

执行清单：领取事务登记 writing 执行；发布/确定性失败关闭为 closed；
结果不确定保持 unknown。清理任务 done 前必须确认无 open 执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012_index_executions"
down_revision: Union[str, Sequence[str], None] = "011_cleanup_verified_empty"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_index_executions",
        sa.Column("execution_id", sa.String(length=36), nullable=False,
                  comment="执行ID"),
        sa.Column("document_id", sa.String(length=36), nullable=False, comment="文档ID"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="所属用户ID"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="执行代次"),
        sa.Column("token", sa.String(length=64), nullable=False, comment="执行令牌"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="writing",
                  comment="writing / closed / unknown"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True, comment="关闭时间"),
        sa.Column("close_reason", sa.String(length=200), nullable=True,
                  comment="关闭依据/错误码"),
        sa.PrimaryKeyConstraint("execution_id"),
        sa.UniqueConstraint("document_id", "generation", name="uq_execution_doc_gen"),
    )
    op.create_index("ix_document_index_executions_document_id",
                    "document_index_executions", ["document_id"])
    op.create_index("ix_execution_status", "document_index_executions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_execution_status", table_name="document_index_executions")
    op.drop_index("ix_document_index_executions_document_id",
                  table_name="document_index_executions")
    op.drop_table("document_index_executions")
