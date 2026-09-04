"""health 路由测试 —— 存活/就绪/MySQL/Redis/向量库/模型检查（无鉴权）。

check_mysql_connection / check_redis_connection / VectorStoreService /
embed_model 打桩，覆盖 ok 与 degraded 两条路径。
"""
import asyncio

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI


@pytest_asyncio.fixture
async def client(monkeypatch):
    from app.router.health import health_router
    import app.router.health as hmod
    from app.utils.auth_utils import get_current_user_id

    # 默认打桩为可用
    async def _mysql_ok():
        return True

    async def _redis_ok():
        return True

    monkeypatch.setattr(hmod, "check_mysql_connection", _mysql_ok)
    monkeypatch.setattr(hmod, "check_redis_connection", _redis_ok)

    # 深度检查需运维身份：统一放行 + 重置模块级短缓存，避免跨用例污染
    monkeypatch.setattr(hmod, "_is_ops_user", lambda user_id: True)
    hmod._model_cache.update({"value": None, "at": 0.0})
    hmod._vector_cache.update({"value": None, "at": 0.0})

    app = FastAPI()
    app.include_router(health_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    # /model 与 /vector-store 需认证：覆盖 get_current_user_id
    app.dependency_overrides[get_current_user_id] = lambda: "ops-user-1"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._hmod = hmod
        yield c


# ---------- 存活 ----------

@pytest.mark.asyncio
async def test_live_ok(client):
    r = await client.get("/api/v1/health/live")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "ok"


# ---------- 就绪 ----------

@pytest.mark.asyncio
async def test_ready_ok(client):
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_redis_down(client, monkeypatch):
    async def _redis_down():
        return False
    monkeypatch.setattr(client._hmod, "check_redis_connection", _redis_down)
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 503
    assert r.json()["data"]["redis"] == "unavailable"


@pytest.mark.asyncio
async def test_ready_mysql_down(client, monkeypatch):
    async def _mysql_down():
        return False
    monkeypatch.setattr(client._hmod, "check_mysql_connection", _mysql_down)
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 503
    assert r.json()["data"]["mysql"] == "unavailable"


# ---------- 组件检查 ----------

@pytest.mark.asyncio
async def test_db_ok(client):
    r = await client.get("/api/v1/health/db")
    assert r.status_code == 200
    assert r.json()["data"]["component"] == "mysql"


@pytest.mark.asyncio
async def test_db_failed(client, monkeypatch):
    async def _mysql_down():
        return False
    monkeypatch.setattr(client._hmod, "check_mysql_connection", _mysql_down)
    r = await client.get("/api/v1/health/db")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_redis_ok(client):
    r = await client.get("/api/v1/health/redis")
    assert r.status_code == 200
    assert r.json()["data"]["component"] == "redis"


@pytest.mark.asyncio
async def test_redis_failed(client, monkeypatch):
    async def _redis_down():
        return False
    monkeypatch.setattr(client._hmod, "check_redis_connection", _redis_down)
    r = await client.get("/api/v1/health/redis")
    assert r.status_code == 503


# ---------- 向量库 ----------

@pytest.mark.asyncio
async def test_vector_store_degraded(client, monkeypatch):
    from types import SimpleNamespace as NS
    fake_vs = NS(
        is_degraded=lambda: True,
        degraded_reason=lambda: "chroma 不可用",
    )
    from app.rag import vector_store as vs_mod
    monkeypatch.setattr(vs_mod, "VectorStoreService", fake_vs)
    r = await client.get("/api/v1/health/vector-store")
    assert r.status_code == 503
    assert r.json()["data"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_vector_store_ok(client, monkeypatch):
    from types import SimpleNamespace as NS

    class FakeVS:
        @staticmethod
        def is_degraded():
            return False

        @staticmethod
        def degraded_reason():
            return ""

        def __init__(self):
            self.vectors_store = NS(_collection=NS(count=lambda: 7))

    from app.rag import vector_store as vs_mod
    monkeypatch.setattr(vs_mod, "VectorStoreService", FakeVS)
    r = await client.get("/api/v1/health/vector-store")
    assert r.status_code == 200
    assert r.json()["data"]["document_count"] == 7


@pytest.mark.asyncio
async def test_vector_store_exception(client, monkeypatch):
    def _boom():
        raise RuntimeError("连接失败")
    from app.rag import vector_store as vs_mod
    monkeypatch.setattr(vs_mod, "VectorStoreService", _boom)
    r = await client.get("/api/v1/health/vector-store")
    assert r.status_code == 503


# ---------- 模型 ----------

@pytest.mark.asyncio
async def test_model_ok(client, monkeypatch):
    from types import SimpleNamespace as NS
    fake_embed = NS(embed_query=lambda t: [0.1, 0.2, 0.3])
    monkeypatch.setattr("app.utils.factory.embed_model", fake_embed)
    r = await client.get("/api/v1/health/model")
    assert r.status_code == 200
    assert r.json()["data"]["dimensions"] == 3


@pytest.mark.asyncio
async def test_model_empty_result(client, monkeypatch):
    from types import SimpleNamespace as NS
    fake_embed = NS(embed_query=lambda t: [])
    monkeypatch.setattr("app.utils.factory.embed_model", fake_embed)
    r = await client.get("/api/v1/health/model")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_model_exception(client, monkeypatch):
    from types import SimpleNamespace as NS

    def _boom(t):
        raise RuntimeError("embedding 服务挂了")
    fake_embed = NS(embed_query=_boom)
    monkeypatch.setattr("app.utils.factory.embed_model", fake_embed)
    r = await client.get("/api/v1/health/model")
    assert r.status_code == 503


# ---------- 工具函数 ----------

@pytest.mark.asyncio
async def test_safe_check_redis_timeout(monkeypatch):
    from app.router.health import _safe_check_redis

    async def _slow():
        await asyncio.sleep(1)
        return True
    monkeypatch.setattr("app.router.health.check_redis_connection", _slow)
    assert await _safe_check_redis(timeout=0.05) is False


# ---------- 深度检查鉴权与脱敏 ----------

@pytest.mark.asyncio
async def test_deep_check_anonymous_rejected():
    """匿名不能执行深度探测（/model 会触发收费 embedding 调用）。"""
    from app.router.health import health_router
    from fastapi import FastAPI
    import httpx

    app = FastAPI()
    app.include_router(health_router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        r = await c.get("/api/v1/health/model")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_vector_store_exception_no_secret_leak(client, monkeypatch):
    def _boom():
        raise RuntimeError("secret-value-ABC123")
    from app.rag import vector_store as vs_mod
    monkeypatch.setattr(vs_mod, "VectorStoreService", _boom)
    r = await client.get("/api/v1/health/vector-store")
    assert r.status_code == 503
    assert "secret-value-ABC123" not in r.text


@pytest.mark.asyncio
async def test_model_exception_no_secret_leak(client, monkeypatch):
    from types import SimpleNamespace as NS

    def _boom(t):
        raise RuntimeError("sk-abcdefghijklmnopqrstuvwxyz1234567890")
    fake_embed = NS(embed_query=_boom)
    monkeypatch.setattr("app.utils.factory.embed_model", fake_embed)
    r = await client.get("/api/v1/health/model")
    assert r.status_code == 503
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in r.text
