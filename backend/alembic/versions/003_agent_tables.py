"""add agent tables for M4 observability

Revision ID: 003_agent_tables
Revises: 002_document_index
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003_agent_tables"
down_revision: Union[str, Sequence[str], None] = "002_document_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agent_runs 表
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False, comment="运行ID（UUID）"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户ID"),
        sa.Column("session_id", sa.String(length=64), nullable=True, comment="会话ID"),
        sa.Column("space_id", sa.String(length=36), nullable=True, comment="空间ID"),
        sa.Column("query", sa.Text(), nullable=False, comment="用户查询"),
        sa.Column("query_type", sa.String(length=30), nullable=True, comment="查询类型"),
        sa.Column("answer", sa.Text(), nullable=True, comment="生成的答案"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="started", comment="运行状态"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误信息"),
        sa.Column("retrieval_rounds", sa.Integer(), server_default="0", comment="检索轮次"),
        sa.Column("evidence_count", sa.Integer(), server_default="0", comment="证据数量"),
        sa.Column("citation_count", sa.Integer(), server_default="0", comment="引用数量"),
        sa.Column("total_time_ms", sa.Integer(), nullable=True, comment="总耗时（毫秒）"),
        sa.Column("model_config", sa.JSON(), nullable=True, comment="模型配置"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), comment="创建时间"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"], unique=False)
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"], unique=False)
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"], unique=False)
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"], unique=False)

    # agent_steps 表
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False, comment="运行ID"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户ID"),
        sa.Column("phase", sa.String(length=30), nullable=False, comment="执行阶段"),
        sa.Column("step_data", sa.JSON(), nullable=True, comment="步骤数据"),
        sa.Column("duration_ms", sa.Integer(), nullable=True, comment="耗时（毫秒）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), comment="创建时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"], unique=False)
    op.create_index("ix_agent_steps_user_id", "agent_steps", ["user_id"], unique=False)

    # agent_feedback 表
    op.create_table(
        "agent_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False, comment="运行ID"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="用户ID"),
        sa.Column("rating", sa.Integer(), nullable=False, comment="评分（1-5）"),
        sa.Column("comment", sa.Text(), nullable=True, comment="评论"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), comment="创建时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_feedback_run_id", "agent_feedback", ["run_id"], unique=False)
    op.create_index("ix_agent_feedback_user_id", "agent_feedback", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_agent_feedback_user_id", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_run_id", table_name="agent_feedback")
    op.drop_table("agent_feedback")

    op.drop_index("ix_agent_steps_user_id", table_name="agent_steps")
    op.drop_index("ix_agent_steps_run_id", table_name="agent_steps")
    op.drop_table("agent_steps")

    op.drop_index("ix_agent_runs_created_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_table("agent_runs")
