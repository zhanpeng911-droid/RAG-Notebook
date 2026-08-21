"""
数据库配置 -- MySQL 异步连接配置。

连接池配置：
- pool_size: 10（基础连接数）
- max_overflow: 20（最大溢出连接）
- pool_recycle: 3600（连接回收时间，防止 MySQL 断开空闲连接）
- pool_pre_ping: True（使用前检测连接有效性）

SQL 注入防护说明：
本项目所有数据库查询均通过 SQLAlchemy ORM 参数化执行（select/where/and_ 等），
不拼接原始 SQL。唯一的 text() 调用为健康检查 "SELECT 1"（无用户输入），安全。
Repository 层禁止使用 text() 拼接用户可控参数。

表结构变更请使用 Alembic：
    alembic upgrade head
启动时只做连通性检查，不再 create_all。
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from app.models.chat_history import Base
from app.models.note import Note  # noqa: F401
from app.models.review_record import ReviewRecord  # noqa: F401
from app.models.organization import Organization, OrganizationMember  # noqa: F401
from app.models.space import Space  # noqa: F401
from app.models.space_document import SpaceDocument  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.document_index import DocumentIndex  # noqa: F401
from app.models.agent_run import AgentRun, AgentStep, AgentFeedback  # noqa: F401
from app.models.runtime_config import RuntimeConfig  # noqa: F401
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


async def init_db():
    """启动时校验数据库连通性。Schema 由 Alembic 管理，不在此 create_all。"""
    async with async_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


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
