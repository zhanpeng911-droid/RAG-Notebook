"""
RAG-Notebook 后端应用入口。

启动流程（lifespan）：
1. 校验关键配置（数据库、Redis、LLM 等）
2. 初始化 FastAPI 实例
3. 注册 CORS 中间件
4. 注册 API 路由（chat、knowledge、note、review 等）
5. 注册全局异常处理器
6. lifespan startup：初始化数据库、会话管理器
7. lifespan shutdown：关闭 Redis 连接
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from app.db.db_config import init_db
from app.db.redis_config import close_redis
from app.router.chat import chat_router
from app.router.knowledge_router import knowledge_router
from app.router.health import health_router
from app.router.user import user_router
from app.router.note_router import note_router
from app.router.review_router import review_router
from app.router.org_router import org_router
from app.router.space_router import space_router
from app.router.audit_router import audit_router

from app.services.database_session_manager import init_database_session_manager

from app.core.failed_response_register import register_exception_handlers
from app.core.logger_handler import logger
from app.config.validator import validate_startup_config

# 启动时验证配置（pydantic-settings 自动从 .env 加载）
app_settings = validate_startup_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理（替代已弃用的 @app.on_event）。

    startup 阶段：初始化数据库、会话管理器
    shutdown 阶段：关闭 Redis 连接
    """
    # === startup ===
    logger.info("应用启动中...")
    logger.info(f"环境: {app_settings.ENV}, LLM类型: {app_settings.LLM_TYPE}")

    try:
        await init_db()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise RuntimeError("数据库初始化失败") from e

    try:
        await init_database_session_manager()
        logger.info("会话管理器初始化完成")
    except Exception as e:
        logger.error(f"会话管理器初始化失败: {e}")
        raise RuntimeError("会话管理器初始化失败") from e

    # Redis：延迟初始化，首次请求时连接（避免阻塞启动）
    logger.info("Redis 将在首次请求时连接")

    logger.info("应用启动完成")

    yield  # 应用运行期间

    # === shutdown ===
    try:
        await close_redis()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """请求耗时中间件 —— 在响应头中添加 X-Process-Time"""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time, 4))
    return response


# 集成API路由
app.include_router(chat_router)
app.include_router(knowledge_router)
app.include_router(health_router)
app.include_router(user_router)
app.include_router(note_router)
app.include_router(review_router)
app.include_router(org_router)
app.include_router(space_router)
app.include_router(audit_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册全局异常处理器
register_exception_handlers(app)


