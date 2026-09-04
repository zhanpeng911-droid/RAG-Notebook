"""add chat_sessions.deleted_at tombstone

Revision ID: 007_chat_session_deleted_at
Revises: 006_document_index_deleting
Create Date: 2026-09-04

会话删除从"物理删除行"改为"保留最小 tombstone（deleted_at + 清空标题）"，
防止删除后迟到的写入把会话重建出来（F03）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007_chat_session_deleted_at"
down_revision: Union[str, Sequence[str], None] = "006_document_index_deleting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="删除时间（tombstone）"),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "deleted_at")
