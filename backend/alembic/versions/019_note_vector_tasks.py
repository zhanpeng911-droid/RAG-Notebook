"""add durable note vector synchronization outbox (P03)

Revision ID: 019_note_vector_tasks
Revises: 018_insight_dispatch_fairness
Create Date: 2026-10-27

The note row and its vector task are committed together. The immediate Celery
call remains a fast path; Beat retries this table after broker/API failures.

Only additive. Downgrade drops the outbox table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019_note_vector_tasks"
down_revision: Union[str, Sequence[str], None] = "018_insight_dispatch_fairness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "note_vector_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False, server_default="upsert"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "note_id", name="uq_note_vector_owner_note"),
    )
    op.create_index("ix_note_vector_tasks_owner_user_id",
                    "note_vector_tasks", ["owner_user_id"])
    op.create_index("ix_note_vector_tasks_note_id",
                    "note_vector_tasks", ["note_id"])
    op.create_index("ix_note_vector_due", "note_vector_tasks",
                    ["status", "next_retry_at"])
    op.create_index("ix_note_vector_owner", "note_vector_tasks",
                    ["owner_user_id", "note_id"])


def downgrade() -> None:
    op.drop_index("ix_note_vector_owner", table_name="note_vector_tasks")
    op.drop_index("ix_note_vector_due", table_name="note_vector_tasks")
    op.drop_index("ix_note_vector_tasks_note_id", table_name="note_vector_tasks")
    op.drop_index("ix_note_vector_tasks_owner_user_id", table_name="note_vector_tasks")
    op.drop_table("note_vector_tasks")
