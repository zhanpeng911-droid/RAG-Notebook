"""
Redis 配置 —— 异步 Redis 连接管理，带熔断器保护。

熔断器机制：
- 连接失败后触发熔断，10 秒内直接返回 None（避免反复重试）
- 连接成功后关闭熔断
- 所有 Redis 操作在不可用时快速降级（返回 None/False）

超时配置：
- 连接超时：0.3s
- 读写超时：0.3s
"""
import asyncio
import json
import time
from typing import Any

import redis.asyncio as redis
from app.config.validator import get_settings

settings = get_settings()
REDIS_HOST = settings.REDIS_HOST or "localhost"
REDIS_PORT = settings.REDIS_PORT
REDIS_DB = settings.REDIS_DB
REDIS_PASSWORD = settings.REDIS_PASSWORD or None

# 全局 Redis 客户端 + 初始化锁（双重检查锁定）
redis_client = None
_redis_lock = asyncio.Lock()

# 熔断器状态
_last_fail_time: float = 0
_circuit_open_until: float = 0
_logged_unavailable: bool = False
_CIRCUIT_BREAK_DURATION = 10  # 熔断持续秒数
_CONNECT_TIMEOUT = 0.3        # 连接超时
_SOCKET_TIMEOUT = 0.3         # 读写超时


def _is_circuit_open() -> bool:
    """检查熔断器是否处于打开状态（失败后 10 秒内打开）"""
    return time.time() < _circuit_open_until


def _record_failure():
    """记录失败，触发熔断"""
    global _circuit_open_until, _logged_unavailable
    _circuit_open_until = time.time() + _CIRCUIT_BREAK_DURATION
    if not _logged_unavailable:
        _logged_unavailable = True


def _record_success():
    """记录成功，关闭熔断"""
    global _circuit_open_until, _logged_unavailable
    _circuit_open_until = 0
    _logged_unavailable = False


async def connect_redis():
    """
    连接 Redis —— 带熔断器和双重检查锁定。

    流程：
    1. 熔断器打开期间直接返回 None
    2. 已有 client 时验证连接是否有效
    3. 无 client 时创建新连接并验证
    """
    global redis_client

    if _is_circuit_open():
        return None

    if redis_client is not None:
        try:
            await asyncio.wait_for(redis_client.ping(), timeout=0.3)
            return redis_client
        except Exception:
            try:
                await redis_client.aclose()
            except Exception:
                pass
            redis_client = None
            _record_failure()
            return None

    async with _redis_lock:
        if redis_client is not None:
            return redis_client
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_SOCKET_TIMEOUT,
            retry_on_timeout=False,
        )
        try:
            await asyncio.wait_for(client.ping(), timeout=0.3)
            redis_client = client
            _record_success()
            return redis_client
        except Exception:
            _record_failure()
            return None


async def close_redis():
    """关闭 Redis 连接 —— 在 shutdown 事件中调用"""
    global redis_client
    if redis_client:
        try:
            await redis_client.aclose()
        except Exception:
            pass
        redis_client = None


async def safe_redis_ping() -> bool:
    """安全检查 Redis 是否可用（0.5s 超时，失败触发熔断）"""
    try:
        client = await connect_redis()
        if client is None:
            return False
        await asyncio.wait_for(client.ping(), timeout=0.5)
        _record_success()
        return True
    except Exception:
        _record_failure()
        return False


async def check_redis_connection() -> bool:
    """健康检查 —— 1 秒超时，失败时清除坏连接"""
    global redis_client
    try:
        client = await connect_redis()
        if client is None:
            return False
        await asyncio.wait_for(client.ping(), timeout=1.0)
        _record_success()
        return True
    except Exception:
        _record_failure()
        redis_client = None
        return False


def is_redis_available() -> bool:
    """同步检查 Redis 是否可用（仅查熔断状态，不发起网络请求）"""
    return not _is_circuit_open()


async def get_redis_cache_str(key: str) -> str | None:
    """获取 Redis 缓存（字符串类型），不可用时返回 None"""
    try:
        client = await connect_redis()
        if client is None:
            return None
        return await client.get(key)
    except Exception:
        return None


async def get_redis_cache_json(key: str) -> dict | None:
    """获取 Redis 缓存（JSON 类型），不可用时返回 None"""
    try:
        client = await connect_redis()
        if client is None:
            return None
        data = await client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception:
        return None


async def set_redis_cache(key: str, value: Any, expire: int = 3600) -> bool:
    """设置 Redis 缓存，不可用时返回 False"""
    try:
        client = await connect_redis()
        if client is None:
            return False
        if isinstance(value, str):
            await client.set(key, value, ex=expire)
        elif isinstance(value, (dict, list)):
            await client.set(key, json.dumps(value, ensure_ascii=False), ex=expire)
        else:
            await client.set(key, str(value), ex=expire)
        return True
    except Exception:
        return False
