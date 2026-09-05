"""add agent_runs.heartbeat_at for R6C lease

Revision ID: 009_agent_run_heartbeat
Revises: 008_document_index_generation
Create Date: 2026-09-05

Agent 流式执行中定期更新 heartbeat_at；过期回收任务以
COALESCE(heartbeat_at, created_at) 判定孤儿运行（此前仅按 created_at，
无法区分"仍在执行"与"崩溃遗留"）。列内容为运行时数据，downgrade 直接
删除是安全的（回收逻辑回退为按 created_at 判定）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009_agent_run_heartbeat"
down_revision: Union[str, Sequence[str], None] = "008_document_index_generation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True,
                  comment="最近心跳时间（执行中定期更新；过期回收依据）"),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "heartbeat_at")
