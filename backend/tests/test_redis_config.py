"""redis_config 直接单测 —— 连接管理、熔断器、缓存读写。

redis.Redis monkeypatch 为假客户端；模块全局状态（redis_client/熔断时间）
每用例重置。
"""
import json
import time

import pytest

import app.db.redis_config as rc


class _FakeRedis:
    """redis.Redis 替身。default_ping 可预设为异常以模拟连接失败。"""

    instances = []
    default_ping = True

    def __init__(self, **kwargs):
        self.ping_result = _FakeRedis.default_ping
        self.closed = False
        self.store = {}
        _FakeRedis.instances.append(self)

    async def ping(self):
        if isinstance(self.ping_result, Exception):
            raise self.ping_result
        return self.ping_result

    async def aclose(self):
        self.closed = True

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _FakeRedis.instances = []
    _FakeRedis.default_ping = True
    monkeypatch.setattr(rc, "redis_client", None)
    monkeypatch.setattr(rc, "_circuit_open_until", 0)
    monkeypatch.setattr(rc, "_logged_unavailable", False)
    monkeypatch.setattr(rc.redis, "Redis", _FakeRedis)
    yield


# ---------- 连接管理 ----------

@pytest.mark.asyncio
async def test_connect_circuit_open_returns_none(monkeypatch):
    monkeypatch.setattr(rc, "_circuit_open_until", time.time() + 100)
    assert await rc.connect_redis() is None


@pytest.mark.asyncio
async def test_connect_new_client_ok():
    client = await rc.connect_redis()
    assert client is not None
    assert rc.redis_client is client
    assert rc._circuit_open_until == 0  # 成功关闭熔断


@pytest.mark.asyncio
async def test_connect_new_client_ping_fail():
    _FakeRedis.default_ping = RuntimeError("连不上")
    assert await rc.connect_redis() is None
    assert rc.redis_client is None
    assert rc._is_circuit_open()


@pytest.mark.asyncio
async def test_connect_existing_client_ok():
    await rc.connect_redis()
    again = await rc.connect_redis()
    assert again is rc.redis_client


@pytest.mark.asyncio
async def test_connect_existing_client_ping_fail():
    await rc.connect_redis()
    old = rc.redis_client
    old.ping_result = RuntimeError("挂了")
    assert await rc.connect_redis() is None
    assert old.closed is True
    assert rc.redis_client is None


@pytest.mark.asyncio
async def test_connect_double_check_lock(monkeypatch):
    # 双重检查锁定：已有 client 时直接复用
    existing = _FakeRedis()
    monkeypatch.setattr(rc, "redis_client", existing)
    out = await rc.connect_redis()
    assert out is existing


@pytest.mark.asyncio
async def test_close_redis():
    await rc.connect_redis()
    await rc.close_redis()
    assert rc.redis_client is None


# ---------- 健康检查 ----------

@pytest.mark.asyncio
async def test_safe_redis_ping_ok():
    assert await rc.safe_redis_ping() is True


@pytest.mark.asyncio
async def test_safe_redis_ping_client_none(monkeypatch):
    monkeypatch.setattr(rc, "_circuit_open_until", time.time() + 100)
    assert await rc.safe_redis_ping() is False


@pytest.mark.asyncio
async def test_safe_redis_ping_exception():
    _FakeRedis.default_ping = RuntimeError("超时")
    assert await rc.safe_redis_ping() is False


@pytest.mark.asyncio
async def test_check_redis_connection_ok():
    assert await rc.check_redis_connection() is True


@pytest.mark.asyncio
async def test_check_redis_connection_fail():
    _FakeRedis.default_ping = RuntimeError("挂了")
    assert await rc.check_redis_connection() is False


def test_is_redis_available(monkeypatch):
    assert rc.is_redis_available() is True
    monkeypatch.setattr(rc, "_circuit_open_until", time.time() + 10)
    assert rc.is_redis_available() is False


# ---------- 缓存读写 ----------

@pytest.mark.asyncio
async def test_get_cache_str():
    client = await rc.connect_redis()
    client.store["k"] = "v1"
    assert await rc.get_redis_cache_str("k") == "v1"


@pytest.mark.asyncio
async def test_get_cache_str_unavailable(monkeypatch):
    monkeypatch.setattr(rc, "_circuit_open_until", time.time() + 100)
    assert await rc.get_redis_cache_str("k") is None


@pytest.mark.asyncio
async def test_get_cache_json():
    client = await rc.connect_redis()
    client.store["k"] = json.dumps({"a": 1})
    assert await rc.get_redis_cache_json("k") == {"a": 1}


@pytest.mark.asyncio
async def test_get_cache_json_empty():
    client = await rc.connect_redis()
    client.store["k"] = None
    assert await rc.get_redis_cache_json("k") is None


@pytest.mark.asyncio
async def test_set_cache_str():
    assert await rc.set_redis_cache("k", "字符串") is True
    assert rc.redis_client.store["k"] == "字符串"


@pytest.mark.asyncio
async def test_set_cache_dict():
    assert await rc.set_redis_cache("k", {"a": 1}) is True
    assert json.loads(rc.redis_client.store["k"]) == {"a": 1}


@pytest.mark.asyncio
async def test_set_cache_other():
    assert await rc.set_redis_cache("k", 42) is True
    assert rc.redis_client.store["k"] == "42"


@pytest.mark.asyncio
async def test_set_cache_unavailable(monkeypatch):
    monkeypatch.setattr(rc, "_circuit_open_until", time.time() + 100)
    assert await rc.set_redis_cache("k", "v") is False
