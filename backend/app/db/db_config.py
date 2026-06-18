"""
数据库配置 —— MySQL 异步连接配置。

连接池配置：
- pool_size: 10（基础连接数）
- max_overflow: 20（最大溢出连接）
- pool_recycle: 3600（连接回收时间，防止 MySQL 断开空闲连接）
- pool_pre_ping: True（使用前检测连接有效性）
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from app.models.chat_history import Base
from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.models.organization import Organization, OrganizationMember
from app.models.space import Space
from app.models.space_document import SpaceDocument
from app.models.audit_log import AuditLog
from app.config.validator import get_settings

# 从统一配置读取
settings = get_settings()
ASYNC_DATABASE_URL = settings.mysql_url

# 创建异步引擎 - 添加连接池稳定性和超时配置
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,       # 连接回收时间（秒），防止MySQL断开空闲连接
    pool_pre_ping=True,      # 使用前ping检测连接有效性
    connect_args={
        "connect_timeout": 10,  # 连接超时10秒
    },
    echo=False  # 关闭SQL日志减少噪音
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# 初始化数据库，创建所有表
async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# 依赖项
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()




async def check_mysql_connection() -> bool:
    """检查MySQL连接（带重试）"""
    import asyncio
    for attempt in range(3):
        try:
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1 * (attempt + 1))
            else:
                from app.core.logger_handler import logger as _logger
                _logger.error(f"MySQL连接失败(重试3次后): {e}")
    return False
