"""runtime_config_router 测试 —— 检索参数热更新：仅 RUNTIME_CONFIG_ADMIN 可改。

runtime_config.set_values/reset_values 直接使用 db_config.AsyncSessionLocal
持久化，测试中将其 monkeypatch 为内存 SQLite 工厂；audit 走 get_db（override）。
"""
import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.config.validator import get_settings
from app.models.chat_history import Base

SECRET = get_settings().SECRET_KEY
ADMIN = "u-admin-0000-0000-000000000001"
USER_A = "u-aaaa-0000-0000-000000000002"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client(monkeypatch):
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.runtime_config_router import runtime_config_router
    import app.db.db_config as dbc

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)
    # set_values/reset_values 内部使用 db_config.AsyncSessionLocal
    monkeypatch.setattr(dbc, "AsyncSessionLocal", factory)

    app = FastAPI()
    app.include_router(runtime_config_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 缓存 settings 实例，指定管理员
    monkeypatch.setattr(get_settings(), "RUNTIME_CONFIG_ADMIN_USER_IDS", ADMIN)

    # 审计日志写入需要 audit_logs 表，已在 Base 中建好
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        yield c
    await engine.dispose()


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.get("/api/v1/admin/runtime-config")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/admin/runtime-config",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


# ---------- 查看 ----------

@pytest.mark.asyncio
async def test_get_all_admin(client):
    r = await client.get("/api/v1/admin/runtime-config", headers=_auth(ADMIN))
    assert r.status_code == 200
    keys = [p["key"] for p in r.json()["data"]["params"]]
    assert "retrieval.top_k_baseline" in keys


@pytest.mark.asyncio
async def test_get_all_non_admin_ok(client):
    # 查看不需要 admin，登录即可
    r = await client.get("/api/v1/admin/runtime-config", headers=_auth(USER_A))
    assert r.status_code == 200


# ---------- 更新 ----------

@pytest.mark.asyncio
async def test_update_non_admin_403(client):
    r = await client.put("/api/v1/admin/runtime-config", headers=_auth(USER_A),
                         json={"values": {"retrieval.top_k_baseline": 6}})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_update_empty_values_400(client):
    r = await client.put("/api/v1/admin/runtime-config", headers=_auth(ADMIN),
                         json={"values": {}})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_ok(client):
    r = await client.put("/api/v1/admin/runtime-config", headers=_auth(ADMIN),
                         json={"values": {"retrieval.top_k_baseline": 6,
                                          "grader.confidence_high": 0.8}})
    assert r.status_code == 200
    assert r.json()["data"]["values"]["retrieval.top_k_baseline"] == 6


@pytest.mark.asyncio
async def test_update_invalid_value_400(client):
    r = await client.put("/api/v1/admin/runtime-config", headers=_auth(ADMIN),
                         json={"values": {"retrieval.top_k_baseline": 999}})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_unknown_key_400(client):
    r = await client.put("/api/v1/admin/runtime-config", headers=_auth(ADMIN),
                         json={"values": {"no.such.key": 1}})
    assert r.status_code == 400


# ---------- 重置 ----------

@pytest.mark.asyncio
async def test_reset_non_admin_403(client):
    r = await client.post("/api/v1/admin/runtime-config/reset",
                          headers=_auth(USER_A), json={"keys": []})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reset_all_ok(client):
    # 先设置一个值，再重置全部
    await client.put("/api/v1/admin/runtime-config", headers=_auth(ADMIN),
                     json={"values": {"retrieval.top_k_baseline": 8}})
    r = await client.post("/api/v1/admin/runtime-config/reset",
                          headers=_auth(ADMIN), json={"keys": []})
    assert r.status_code == 200
    assert "retrieval.top_k_baseline" in r.json()["data"]["reset"]


@pytest.mark.asyncio
async def test_reset_specific_key_ok(client):
    r = await client.post("/api/v1/admin/runtime-config/reset",
                          headers=_auth(ADMIN),
                          json={"keys": ["retrieval.chroma_k"]})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_reset_unknown_key_400(client):
    r = await client.post("/api/v1/admin/runtime-config/reset",
                          headers=_auth(ADMIN),
                          json={"keys": ["no.such.key"]})
    assert r.status_code == 400


# ---------- 直接调用端点函数 ----------

from app.router.runtime_config_router import (  # noqa: E402
    get_runtime_configs, update_runtime_configs, reset_runtime_configs,
    RuntimeConfigUpdateRequest, RuntimeConfigResetRequest,
)
from fastapi import HTTPException as FE  # noqa: E402


@pytest.mark.asyncio
async def test_direct_get(client):
    resp = await get_runtime_configs(ADMIN)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_update_ok(client):
    async with client._factory() as db:
        resp = await update_runtime_configs(
            RuntimeConfigUpdateRequest(values={"retrieval.top_k_baseline": 5}),
            ADMIN, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_update_forbidden(client):
    async with client._factory() as db:
        with pytest.raises(FE) as ei:
            await update_runtime_configs(
                RuntimeConfigUpdateRequest(values={"retrieval.top_k_baseline": 5}),
                USER_A, db)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_reset_ok(client):
    async with client._factory() as db:
        resp = await reset_runtime_configs(
            RuntimeConfigResetRequest(keys=[]), ADMIN, db)
        assert resp.status_code == 200
