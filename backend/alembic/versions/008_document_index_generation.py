"""add document_index generation columns for R2B execution identity

Revision ID: 008_document_index_generation
Revises: 007_chat_session_deleted_at
Create Date: 2026-09-05

- generation: 执行代次，每次原子 claim 递增；chunk ID 与向量元数据携带该值，
  重复/迟到 worker 无法把旧代次产物当作新代次发布（执行身份）。
- active_generation: 已发布（对外可见）的代次；0 表示尚无已发布向量。
  读取侧过滤与旧代次清理以此为依据。

两列均为派生数据：降级到旧版本应用后旧代码不读取这两列，
向量清理按 user_id+md5 仍然成立，因此 downgrade 直接删列是安全的。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008_document_index_generation"
down_revision: Union[str, Sequence[str], None] = "007_chat_session_deleted_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_index",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0",
                  comment="当前执行代次（每次 claim 递增；0=从未执行）"),
    )
    op.add_column(
        "document_index",
        sa.Column("active_generation", sa.Integer(), nullable=False, server_default="0",
                  comment="已发布（对外可见）的代次；0=尚无已发布向量"),
    )


def downgrade() -> None:
    """回退安全（评审 P1 #6）：存在已发布代次时拒绝执行。

    active_generation 是读取侧区分多代次向量的唯一依据；直接删列后旧
    应用无法判定哪个代次对外可见，回滚期间残留的旧代次切片可能重新
    可见。必须先在受控维护窗口停写，确认已发布文档的旧代次向量清理
    完成（active_generation 归零或数据已归档）后再降级。
    MySQL DDL 隐式提交，检查与 DDL 之间的窗口须停写执行（同 006/007）。
    """
    conn = op.get_bind()
    published = conn.execute(
        sa.text("SELECT COUNT(*) FROM document_index WHERE active_generation > 0")
    ).scalar()
    if published:
        raise RuntimeError(
            f"document_index 存在 {published} 条已发布代次（active_generation > 0），"
            "拒绝降级：删除代次列会丢失读取侧可见性判定依据，残留旧代次切片"
            "可能在回滚后重新可见。请先停写并完成旧代次向量清理后重试。"
        )
    op.drop_column("document_index", "active_generation")
    op.drop_column("document_index", "generation")
