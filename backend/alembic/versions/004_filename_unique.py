"""add unique constraint on (user_id, original_filename)

Revision ID: 004_filename_unique
Revises: 003_agent_tables
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op

revision: str = "004_filename_unique"
down_revision: Union[str, Sequence[str], None] = "003_agent_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_user_original_filename",
        "document_index",
        ["user_id", "original_filename"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_user_original_filename", "document_index", type_="unique")
