"""add knowledge insight / source anchor / graph tables (N0-N3)

Revision ID: 016_knowledge_insights
Revises: 015_legacy_mappings
Create Date: 2026-10-20

新增：
- knowledge_source_versions：来源版本身份
- source_anchors：可核验原文锚点
- knowledge_generation_jobs：知识生成任务
- knowledge_artifacts：结构化知识产物
- knowledge_evidence：结论→锚点证据链
- knowledge_concepts / knowledge_relations：跨文档知识图谱

只追加，不修改既有迁移。downgrade 删除本迁移创建的表。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016_knowledge_insights"
down_revision: Union[str, Sequence[str], None] = "015_legacy_mappings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_source_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("space_id", sa.String(length=36), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=False,
                  server_default="anchor-v1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_id", "generation", name="uq_ksv_source_gen"),
    )
    op.create_index("ix_knowledge_source_versions_owner_user_id",
                    "knowledge_source_versions", ["owner_user_id"])
    op.create_index("ix_knowledge_source_versions_space_id",
                    "knowledge_source_versions", ["space_id"])
    op.create_index("ix_ksv_owner_source", "knowledge_source_versions",
                    ["owner_user_id", "source_type", "source_id"])
    op.create_index("ix_ksv_owner_status", "knowledge_source_versions",
                    ["owner_user_id", "status"])

    op.create_table(
        "source_anchors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("space_id", sa.String(length=36), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_id", sa.String(length=200), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("heading_path", sa.String(length=1000), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("quote_text", sa.Text(), nullable=False),
        sa.Column("quote_hash", sa.String(length=64), nullable=False),
        sa.Column("locator_kind", sa.String(length=20), nullable=False, server_default="chunk"),
        sa.Column("extraction_confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_version_id", "chunk_index", name="uq_anchor_version_chunk"),
    )
    op.create_index("ix_source_anchors_source_version_id", "source_anchors", ["source_version_id"])
    op.create_index("ix_anchor_owner_source_gen", "source_anchors",
                    ["owner_user_id", "source_id", "generation"])
    op.create_index("ix_anchor_version", "source_anchors", ["source_version_id"])

    op.create_table(
        "knowledge_generation_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False,
                  server_default="insight-v1"),
        sa.Column("model_id", sa.String(length=120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_version_id", "artifact_type", name="uq_insight_job_version_type"),
    )
    op.create_index("ix_knowledge_generation_jobs_owner_user_id",
                    "knowledge_generation_jobs", ["owner_user_id"])
    op.create_index("ix_insight_job_owner", "knowledge_generation_jobs",
                    ["owner_user_id", "created_at"])

    op.create_table(
        "knowledge_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("artifact_type", sa.String(length=20), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False, server_default="1.0"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_version_id", "artifact_type", name="uq_artifact_version_type"),
    )
    op.create_index("ix_knowledge_artifacts_owner_user_id", "knowledge_artifacts", ["owner_user_id"])
    op.create_index("ix_artifact_owner_source", "knowledge_artifacts",
                    ["owner_user_id", "source_id"])

    op.create_table(
        "knowledge_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("target_kind", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("anchor_id", sa.String(length=36), nullable=False),
        sa.Column("supporting_quote", sa.Text(), nullable=True),
        sa.Column("quote_hash", sa.String(length=64), nullable=True),
        sa.Column("verification_status", sa.String(length=20), nullable=False,
                  server_default="verified"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "target_kind", "target_id", "anchor_id",
                            name="uq_evidence_target_anchor"),
    )
    op.create_index("ix_knowledge_evidence_owner_user_id", "knowledge_evidence", ["owner_user_id"])
    op.create_index("ix_evidence_artifact", "knowledge_evidence", ["artifact_id"])
    op.create_index("ix_evidence_anchor", "knowledge_evidence", ["anchor_id"])
    op.create_index("ix_evidence_source_version", "knowledge_evidence", ["source_version_id"])

    op.create_table(
        "knowledge_concepts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("space_id", sa.String(length=36), nullable=True),
        sa.Column("space_key", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "space_key", "normalized_name",
                            name="uq_concept_owner_space_name"),
    )
    op.create_index("ix_knowledge_concepts_owner_user_id", "knowledge_concepts", ["owner_user_id"])
    op.create_index("ix_concept_owner_space", "knowledge_concepts", ["owner_user_id", "space_key"])

    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("space_id", sa.String(length=36), nullable=True),
        sa.Column("space_key", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("source_concept_id", sa.String(length=36), nullable=False),
        sa.Column("target_concept_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("verification_state", sa.String(length=20), nullable=False,
                  server_default="auto"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "space_key", "source_concept_id",
                            "target_concept_id", "relation_type",
                            name="uq_relation_owner_space_triple"),
    )
    op.create_index("ix_knowledge_relations_owner_user_id", "knowledge_relations", ["owner_user_id"])
    op.create_index("ix_relation_owner_space", "knowledge_relations", ["owner_user_id", "space_key"])


def downgrade() -> None:
    op.drop_index("ix_relation_owner_space", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_owner_user_id", table_name="knowledge_relations")
    op.drop_table("knowledge_relations")

    op.drop_index("ix_concept_owner_space", table_name="knowledge_concepts")
    op.drop_index("ix_knowledge_concepts_owner_user_id", table_name="knowledge_concepts")
    op.drop_table("knowledge_concepts")

    op.drop_index("ix_evidence_source_version", table_name="knowledge_evidence")
    op.drop_index("ix_evidence_anchor", table_name="knowledge_evidence")
    op.drop_index("ix_evidence_artifact", table_name="knowledge_evidence")
    op.drop_index("ix_knowledge_evidence_owner_user_id", table_name="knowledge_evidence")
    op.drop_table("knowledge_evidence")

    op.drop_index("ix_artifact_owner_source", table_name="knowledge_artifacts")
    op.drop_index("ix_knowledge_artifacts_owner_user_id", table_name="knowledge_artifacts")
    op.drop_table("knowledge_artifacts")

    op.drop_index("ix_insight_job_owner", table_name="knowledge_generation_jobs")
    op.drop_index("ix_knowledge_generation_jobs_owner_user_id", table_name="knowledge_generation_jobs")
    op.drop_table("knowledge_generation_jobs")

    op.drop_index("ix_anchor_version", table_name="source_anchors")
    op.drop_index("ix_anchor_owner_source_gen", table_name="source_anchors")
    op.drop_index("ix_source_anchors_source_version_id", table_name="source_anchors")
    op.drop_table("source_anchors")

    op.drop_index("ix_ksv_owner_status", table_name="knowledge_source_versions")
    op.drop_index("ix_ksv_owner_source", table_name="knowledge_source_versions")
    op.drop_index("ix_knowledge_source_versions_space_id", table_name="knowledge_source_versions")
    op.drop_index("ix_knowledge_source_versions_owner_user_id", table_name="knowledge_source_versions")
    op.drop_table("knowledge_source_versions")
