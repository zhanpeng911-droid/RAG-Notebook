"""add document_index generation columns for R2B execution identity

Revision ID: 008_document_index_generation
Revises: 007_chat_session_deleted_at
Create Date: 2026-09-05

- generation: 执行代次，每次原子 claim 递增；chunk ID 与向量元数据携带该值，
  重复/迟到 worker 无法把旧代次产物当作新代次发布（执行身份）。
- active_generation: 已发布（对外可见）的代次；0 表示尚无已发布向量。
  读取侧过滤与旧代次清理以此为依据。

两列均为派生数据：降级到旧版本应用后旧代码不读取这两列，
向量清理按 user_id+md5 仍然成立，因此 downgrade 直接删列是安全的。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008_document_index_generation"
down_revision: Union[str, Sequence[str], None] = "007_chat_session_deleted_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_index",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0",
                  comment="当前执行代次（每次 claim 递增；0=从未执行）"),
    )
    op.add_column(
        "document_index",
        sa.Column("active_generation", sa.Integer(), nullable=False, server_default="0",
                  comment="已发布（对外可见）的代次；0=尚无已发布向量"),
    )


def downgrade() -> None:
    op.drop_column("document_index", "active_generation")
    op.drop_column("document_index", "generation")
