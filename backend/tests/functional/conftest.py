"""
功能验收套件共享 fixtures —— 全路由 FastAPI app + 内存 SQLite + 真实 JWT。

与单元测试（tests/ 根）分离：CI `--ignore=tests/functional`，发布前手动执行。
Part A 用例无外部依赖（TestClient + 内存库 + 打桩）；Part B 带 @pytest.mark.real_api，
无 LLM key 时显式 skip。
"""
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.config.validator import get_settings
from app.models.chat_history import Base

SECRET = get_settings().SECRET_KEY
USER_A = "u-func-aaaa-0000-000000000001"
USER_B = "u-func-bbbb-0000-000000000002"


def token_for(user_id):
    """按 Django JWT 契约签发 HS256 token。"""
    return jose_jwt.encode({"user_id": user_id, "user_name": "u", "jti": "t-1"},
                           SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    # 测试环境无 Redis，禁用 JWT 黑名单校验（黑名单逻辑由单测覆盖）
    import app.utils.auth_utils as au
    monkeypatch.setattr(au, "JWT_BLACKLIST_CHECK_ENABLED", False)
    # 禁用 Celery 触发（note 创建/更新会 .delay 到 Redis broker → 无 Redis 时挂死）
    from app.config.validator import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "NOTE_VECTOR_INDEX_ENABLED", False)
    monkeypatch.setattr(settings, "NOTE_AUTO_TAG_ENABLED", False)
    yield


@pytest_asyncio.fixture
async def factory(monkeypatch):
    """内存 SQLite 会话工厂 —— async fixture，与用例共用同一事件循环。"""
    import app.db.db_config as dbc

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=engine, class_=AsyncSession,
                             expire_on_commit=False)

    # agent_router / runtime_config 直接使用 db_config.AsyncSessionLocal
    monkeypatch.setattr(dbc, "AsyncSessionLocal", fac)
    yield fac
    await engine.dispose()


@pytest_asyncio.fixture
async def app(factory):
    """组装全路由 FastAPI app，override get_db 为内存库。"""
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.core.failed_response_register import register_exception_handlers
    from app.router.agent_router import agent_router
    from app.router.audit_router import audit_router
    from app.router.chat import chat_router
    from app.router.health import health_router
    from app.router.knowledge_router import knowledge_router
    from app.router.note_router import note_router
    from app.router.org_router import org_router
    from app.router.review_router import review_router
    from app.router.runtime_config_router import runtime_config_router
    from app.router.space_router import space_router
    from app.router.user import user_router

    a = FastAPI()
    for r in (health_router, user_router, note_router, review_router,
              org_router, space_router, audit_router, chat_router,
              knowledge_router, agent_router, runtime_config_router):
        a.include_router(r, prefix="/api/v1")
    register_exception_handlers(a)

    async def _override_get_db():
        async with factory() as s:
            yield s

    a.dependency_overrides[get_db] = _override_get_db
    a.state.factory = factory
    return a


@pytest.fixture
def auth_a():
    return {"Authorization": f"Bearer {token_for(USER_A)}"}


@pytest.fixture
def auth_b():
    return {"Authorization": f"Bearer {token_for(USER_B)}"}


def real_api_available() -> bool:
    """Part B：是否配置了可用 LLM（缺 key 时显式跳过）。

    key 通常在 backend/.env（pydantic-settings 加载），不一定是 OS 环境变量，
    因此同时检查 settings 与 os.getenv。
    """
    import os
    from app.config.validator import get_settings
    s = get_settings()
    if any(os.getenv(v) for v in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                                  "DASHSCOPE_API_KEY")):
        return True
    if s.OPENAI_API_KEY or s.DASHSCOPE_API_KEY or s.CHAT_API_KEY:
        return True
    if s.LLM_TYPE == "OLLAMA":
        return True  # 本地 Ollama 无需 key
    return False


real_api = pytest.mark.skipif(
    not real_api_available(),
    reason="未配置 LLM API Key，跳过真实 LLM 功能验收（Part B）",
)
