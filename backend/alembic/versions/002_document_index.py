"""add document_index table for M0 upload-index decoupling

Revision ID: 002_document_index
Revises: 001_initial
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_document_index"
down_revision: Union[str, Sequence[str], None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_index",
        sa.Column("id", sa.String(length=36), nullable=False, comment="文档索引ID（UUID）"),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="所属用户ID"),
        sa.Column("space_id", sa.String(length=36), nullable=True, comment="所属空间ID（空表示个人知识库）"),
        sa.Column("filename", sa.String(length=500), nullable=False, comment="系统存储文件名"),
        sa.Column("original_filename", sa.String(length=500), nullable=False, comment="用户上传时的原始文件名"),
        sa.Column("file_path", sa.String(length=1000), nullable=False, comment="文件在磁盘上的存储路径"),
        sa.Column("file_size", sa.Integer(), nullable=True, comment="文件大小（字节）"),
        sa.Column("file_type", sa.String(length=50), nullable=True, comment="文件类型（扩展名）"),
        sa.Column("md5", sa.String(length=32), nullable=False, comment="文件MD5摘要"),
        sa.Column(
            "status",
            sa.Enum(
                "uploaded", "parsed", "pending_index", "indexing", "indexed", "index_failed",
                name="document_index_status"
            ),
            nullable=False,
            server_default="uploaded",
            comment="索引状态"
        ),
        sa.Column("chunk_count", sa.Integer(), server_default="0", comment="切片数量"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="索引失败时的错误信息"),
        sa.Column("retry_count", sa.Integer(), server_default="0", comment="重试次数"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), comment="更新时间"),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True, comment="索引完成时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_index_user_id", "document_index", ["user_id"], unique=False)
    op.create_index("ix_document_index_space_id", "document_index", ["space_id"], unique=False)
    op.create_index("ix_document_index_md5", "document_index", ["md5"], unique=False)
    op.create_index("ix_document_index_status", "document_index", ["status"], unique=False)
    op.create_index("ix_document_index_user_md5", "document_index", ["user_id", "md5"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_document_index_user_md5", table_name="document_index")
    op.drop_index("ix_document_index_status", table_name="document_index")
    op.drop_index("ix_document_index_md5", table_name="document_index")
    op.drop_index("ix_document_index_space_id", table_name="document_index")
    op.drop_index("ix_document_index_user_id", table_name="document_index")
    op.drop_table("document_index")
    op.execute("DROP TYPE IF EXISTS document_index_status")
