"""运行时配置模型 —— 检索参数热更新（key-value 存储）。"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func

from app.models.chat_history import Base
from sqlalchemy.orm import Mapped, mapped_column


class RuntimeConfig(Base):
    """运行时配置表：检索参数的持久化覆盖值（不存在的 key 使用代码默认值）。"""
    __tablename__ = "runtime_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 值统一以 JSON 字符串存储（int/float/bool 均可序列化）
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
