"""add legacy_document_mappings table (C batch)

Revision ID: 015_legacy_mappings
Revises: 014_execution_recovery
Create Date: 2026-09-08

存量向量 → 资源身份 的持久映射。唯一键含数据源身份。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015_legacy_mappings"
down_revision: Union[str, Sequence[str], None] = "014_execution_recovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "legacy_document_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False,
                  comment="数据源/collection 身份"),
        sa.Column("legacy_vector_id", sa.String(length=500), nullable=False,
                  comment="旧向量 ID"),
        sa.Column("owner", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("compat_generation", sa.Integer(), nullable=False,
                  server_default="0", comment="兼容代次（0=经核验存量）"),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="active"),
        sa.Column("source_batch", sa.String(length=64), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "legacy_vector_id", name="uq_legacy_source_vector"),
    )
    op.create_index("ix_legacy_mapping_owner", "legacy_document_mappings", ["owner"])


def downgrade() -> None:
    op.drop_index("ix_legacy_mapping_owner", table_name="legacy_document_mappings")
    op.drop_table("legacy_document_mappings")
