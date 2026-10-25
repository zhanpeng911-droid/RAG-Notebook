"""add note graph links, manual relations and mindmap edits (V2 P3/P4)

Revision ID: 017_note_graph
Revises: 016_knowledge_insights
Create Date: 2026-10-20

新增：
- note_link_occurrences：正文 [[...]] 链接出现位置与解析结果
- note_manual_relations：用户显式手动关联
- knowledge_mindmap_edits：导图用户标题/同级顺序修订

只追加，不修改既有迁移。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017_note_graph"
down_revision: Union[str, Sequence[str], None] = "016_knowledge_insights"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "note_link_occurrences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_note_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column("target_note_id", sa.String(length=36), nullable=True),
        sa.Column("raw_token", sa.String(length=500), nullable=False),
        sa.Column("display_text", sa.String(length=300), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_note_link_occurrences_owner_user_id",
                    "note_link_occurrences", ["owner_user_id"])
    op.create_index("ix_note_occ_owner_source", "note_link_occurrences",
                    ["owner_user_id", "source_note_id"])
    op.create_index("ix_note_occ_owner_target", "note_link_occurrences",
                    ["owner_user_id", "target_note_id"])
    op.create_index("ix_note_occ_source_revision", "note_link_occurrences",
                    ["source_note_id", "source_revision"])

    op.create_table(
        "note_manual_relations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_note_id", sa.String(length=36), nullable=False),
        sa.Column("target_note_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=30), nullable=False,
                  server_default="manual"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "source_note_id", "target_note_id",
                            "relation_type", name="uq_note_manual_triple"),
    )
    op.create_index("ix_note_manual_relations_owner_user_id",
                    "note_manual_relations", ["owner_user_id"])
    op.create_index("ix_note_manual_owner_source", "note_manual_relations",
                    ["owner_user_id", "source_note_id"])
    op.create_index("ix_note_manual_owner_target", "note_manual_relations",
                    ["owner_user_id", "target_note_id"])

    op.create_table(
        "knowledge_mindmap_edits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("base_source_version_id", sa.String(length=36), nullable=True),
        sa.Column("node_id", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "source_type", "source_id", "node_id",
                            name="uq_mindmap_edit_node"),
    )
    op.create_index("ix_knowledge_mindmap_edits_owner_user_id",
                    "knowledge_mindmap_edits", ["owner_user_id"])
    op.create_index("ix_mindmap_edit_owner_source", "knowledge_mindmap_edits",
                    ["owner_user_id", "source_type", "source_id"])


def downgrade() -> None:
    op.drop_index("ix_mindmap_edit_owner_source", table_name="knowledge_mindmap_edits")
    op.drop_index("ix_knowledge_mindmap_edits_owner_user_id",
                  table_name="knowledge_mindmap_edits")
    op.drop_table("knowledge_mindmap_edits")

    op.drop_index("ix_note_manual_owner_target", table_name="note_manual_relations")
    op.drop_index("ix_note_manual_owner_source", table_name="note_manual_relations")
    op.drop_index("ix_note_manual_relations_owner_user_id",
                  table_name="note_manual_relations")
    op.drop_table("note_manual_relations")

    op.drop_index("ix_note_occ_source_revision", table_name="note_link_occurrences")
    op.drop_index("ix_note_occ_owner_target", table_name="note_link_occurrences")
    op.drop_index("ix_note_occ_owner_source", table_name="note_link_occurrences")
    op.drop_index("ix_note_link_occurrences_owner_user_id",
                  table_name="note_link_occurrences")
    op.drop_table("note_link_occurrences")
