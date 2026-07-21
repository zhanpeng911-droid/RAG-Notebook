"""
文档索引状态模型 —— 跟踪知识库文档的上传、解析、索引状态。

状态流转：
uploaded → parsed → pending_index → indexing → indexed
                                         → index_failed（可重试）
"""
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, String, DateTime, Text, Integer, Enum, func
from app.models.chat_history import Base


class DocumentIndexStatus(str, PyEnum):
    """文档索引状态枚举"""
    UPLOADED = "uploaded"           # 文件已接收
    PARSED = "parsed"               # 文本解析完成
    PENDING_INDEX = "pending_index" # 等待 embedding 与向量化
    INDEXING = "indexing"           # 正在后台索引
    INDEXED = "indexed"             # 已完成索引，可用于检索
    INDEX_FAILED = "index_failed"   # 索引失败，可重试


class DocumentIndex(Base):
    """文档索引状态表 —— 跟踪每个上传文档的索引生命周期"""
    __tablename__ = "document_index"

    id = Column(String(36), primary_key=True, comment="文档索引ID（UUID）")
    user_id = Column(String(36), nullable=False, index=True, comment="所属用户ID")
    space_id = Column(String(36), nullable=True, index=True, comment="所属空间ID（空表示个人知识库）")

    filename = Column(String(500), nullable=False, comment="系统存储文件名")
    original_filename = Column(String(500), nullable=False, comment="用户上传时的原始文件名")
    file_path = Column(String(1000), nullable=False, comment="文件在磁盘上的存储路径")
    file_size = Column(Integer, nullable=True, comment="文件大小（字节）")
    file_type = Column(String(50), nullable=True, comment="文件类型（扩展名）")
    md5 = Column(String(32), nullable=False, index=True, comment="文件MD5摘要")

    status = Column(
        Enum(DocumentIndexStatus, values_callable=lambda enum_class: [item.value for item in enum_class], name="document_index_status"),
        nullable=False,
        default=DocumentIndexStatus.UPLOADED,
        comment="索引状态"
    )
    chunk_count = Column(Integer, default=0, comment="切片数量")
    error_message = Column(Text, nullable=True, comment="索引失败时的错误信息")
    retry_count = Column(Integer, default=0, comment="重试次数")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间")
    indexed_at = Column(DateTime(timezone=True), nullable=True, comment="索引完成时间")
