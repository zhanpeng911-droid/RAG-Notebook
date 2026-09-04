"""extend document_index.status enum with 'deleting'

Revision ID: 006_document_index_deleting
Revises: 005_runtime_config
Create Date: 2026-09-04

将 document_index.status 的 ENUM 从六个值扩展为包含 'deleting'（删除中），
保留原有六值、NOT NULL、默认值 'uploaded' 与注释。AgentRun.status 是 VARCHAR，
不照搬一份 ENUM 迁移。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006_document_index_deleting"
down_revision: Union[str, Sequence[str], None] = "005_runtime_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATUS = ("uploaded", "parsed", "pending_index", "indexing", "indexed", "index_failed")
_NEW_STATUS = _OLD_STATUS + ("deleting",)


def upgrade() -> None:
    op.alter_column(
        "document_index",
        "status",
        existing_type=sa.Enum(*_OLD_STATUS, name="document_index_status"),
        type_=sa.Enum(*_NEW_STATUS, name="document_index_status"),
        existing_nullable=False,
        existing_server_default=sa.text("'uploaded'"),
        existing_comment="索引状态",
    )


def downgrade() -> None:
    # 危险回退：存在 deleting 行时拒绝缩回 ENUM。
    # 不能把它们批量改成 indexed，也不能删记录来让 downgrade 成功。
    conn = op.get_bind()
    has_deleting = conn.execute(
        sa.text("SELECT COUNT(*) FROM document_index WHERE status = 'deleting'")
    ).scalar()
    if has_deleting:
        raise RuntimeError(
            "存在 deleting 状态的文档索引记录，拒绝缩回 ENUM；请先完成删除清理"
        )
    op.alter_column(
        "document_index",
        "status",
        existing_type=sa.Enum(*_NEW_STATUS, name="document_index_status"),
        type_=sa.Enum(*_OLD_STATUS, name="document_index_status"),
        existing_nullable=False,
        existing_server_default=sa.text("'uploaded'"),
        existing_comment="索引状态",
    )
