"""drop redundant note link indexes on the hot save path (L02)

Revision ID: 020_note_link_write_indexes
Revises: 019_note_vector_tasks
Create Date: 2026-10-27

The owner-first composite indexes already serve graph/link queries. A
standalone owner index and an unused source_revision index only added write
amplification for notes with many links (100 links per save).

Only indexes are removed; no data is changed. Downgrade recreates them.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "020_note_link_write_indexes"
down_revision: Union[str, Sequence[str], None] = "019_note_vector_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_if_exists(name: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    names = {row[2] for row in bind.exec_driver_sql(
        "SHOW INDEX FROM note_link_occurrences"
    )}
    if name in names:
        op.drop_index(name, table_name="note_link_occurrences")


def upgrade() -> None:
    _drop_if_exists("ix_note_link_occurrences_owner_user_id")
    _drop_if_exists("ix_note_occ_source_revision")


def downgrade() -> None:
    op.create_index("ix_note_occ_source_revision", "note_link_occurrences",
                    ["source_note_id", "source_revision"])
    op.create_index("ix_note_link_occurrences_owner_user_id",
                    "note_link_occurrences", ["owner_user_id"])
