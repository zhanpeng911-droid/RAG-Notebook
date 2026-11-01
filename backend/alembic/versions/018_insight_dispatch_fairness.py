"""add compensation dispatch cursor for queued insight jobs (F01/P04)

Revision ID: 018_insight_dispatch_fairness
Revises: 017_note_graph
Create Date: 2026-10-27

The queued-job compensator previously re-selected the same oldest batch on
every Beat tick. ``last_dispatch_at`` records the last compensation attempt so
selection rotates fairly while the job itself stays ``queued`` and retryable.

Only additive. Downgrade removes the cursor column; no job rows are changed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018_insight_dispatch_fairness"
down_revision: Union[str, Sequence[str], None] = "017_note_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_generation_jobs",
        sa.Column("last_dispatch_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_insight_job_last_dispatch",
        "knowledge_generation_jobs",
        ["last_dispatch_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_insight_job_last_dispatch",
                  table_name="knowledge_generation_jobs")
    op.drop_column("knowledge_generation_jobs", "last_dispatch_at")
