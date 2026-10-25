"""
存量向量映射模型（统一方案 C）——旧随机 ID / 固定 ID / MD5 代次 ID → 资源身份。

最小字段：数据源身份、旧向量 ID、owner、document_id、兼容代次、有效状态、
来源批次、证据摘要。唯一键包含数据源身份（不能跨 collection 仅按旧 ID 去重）。
generation=0 只表示经核验的存量版本，不是缺字段通配符。
"""
import uuid
from enum import Enum as PyEnum

from sqlalchemy import Column, String, DateTime, Integer, Text, UniqueConstraint, func

from app.models.chat_history import Base


class LegacyMappingStatus(str, PyEnum):
    ACTIVE = "active"        # 经验证可经映射读取
    UNRESOLVED = "unresolved"  # 归属不清/冲突，等待人工
    REVOKED = "revoked"      # 映射被撤销（新代次发布后旧映射失效）


class LegacyDocumentMapping(Base):
    """存量向量 → 资源身份 的持久映射表"""
    __tablename__ = "legacy_document_mappings"
    __table_args__ = (
        UniqueConstraint("source", "legacy_vector_id", name="uq_legacy_source_vector"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source = Column(String(100), nullable=False, comment="数据源/collection 身份")
    legacy_vector_id = Column(String(500), nullable=False, comment="旧向量 ID")
    owner = Column(String(36), nullable=False, comment="资源 owner（user_id）")
    document_id = Column(String(36), nullable=False, comment="映射到的文档ID")
    compat_generation = Column(Integer, nullable=False, default=0,
                               comment="兼容代次（0=经核验存量版本）")
    status = Column(String(20), nullable=False, default=LegacyMappingStatus.ACTIVE,
                    comment="映射状态")
    source_batch = Column(String(64), nullable=True, comment="来源批次ID")
    evidence_summary = Column(Text, nullable=True, comment="证据摘要（不含正文/密钥）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
