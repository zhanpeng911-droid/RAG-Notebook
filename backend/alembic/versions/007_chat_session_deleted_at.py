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
    """回退安全（S07）：存在 tombstone（deleted_at 非空）时拒绝执行。

    drop_column 会丢失删除判定依据，使旧版本应用把已删除会话当作存在，
    迟到写入即复活会话。必须先安全清退 tombstone（确认无在途写入后
    归档/清理删除标记）再降级；MySQL DDL 隐式提交，本检查与 DDL 之间的
    窗口要求在受控维护窗口内停写执行（与 006 同一运维约束）。
    """
    conn = op.get_bind()
    tombstones = conn.execute(
        sa.text("SELECT COUNT(*) FROM chat_sessions WHERE deleted_at IS NOT NULL")
    ).scalar()
    if tombstones:
        raise RuntimeError(
            f"chat_sessions 存在 {tombstones} 条 tombstone（deleted_at 非空），"
            "拒绝降级：删除列会丢失删除判定依据并可能导致已删会话复活。"
            "请先在受控维护窗口停写并处理 tombstone 后重试。"
        )
    op.drop_column("chat_sessions", "deleted_at")
