"""
基于 Redis 的限流工具 —— 提供路由级限流依赖函数。

使用 Redis 的 INCR + EXPIRE 实现滑动窗口限流，
当 Redis 不可用时自动降级放行（不影响正常请求）。

用法:
    @router.get("/some-endpoint")
    async def endpoint(_: None = Depends(rate_limit(limit=10, window=60))):
        ...
"""
import os

from fastapi import Request, HTTPException

from app.db.redis_config import connect_redis, is_redis_available


def _is_rate_limit_enabled() -> bool:
    """动态读取限流开关（每次调用读取，避免 .env 缓存问题）"""
    return os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"


def rate_limit(limit: int = 1, window: int = 60):
    """
    限流依赖函数 —— 用于 FastAPI 的 Depends()。

    原理：基于 Redis 的滑动窗口计数器。
    - 每个客户端 IP 对应一个 Redis key
    - 第一次请求时设置 EXPIRE（窗口过期时间）
    - 后续请求 INCR 自增计数
    - 超过 limit 则返回 429

    :param limit: 时间窗口内的最大请求数
    :param window: 时间窗口大小（秒）
    :return: FastAPI 依赖函数
    """
    async def dependency(request: Request):
        # 开关关闭或 Redis 不可用时直接放行
        if not _is_rate_limit_enabled() or not is_redis_available():
            return

        # 获取客户端 IP（支持反向代理）
        client_ip = request.client.host
        if not client_ip:
            client_ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or 'unknown'

        key = f"rate_limit:aichat:{client_ip}"

        try:
            redis = await connect_redis()
            if redis is None:
                return

            current = await redis.get(key)
            current = int(current) if current else 0

            if current >= limit:
                raise HTTPException(
                    status_code=429,
                    detail="请求过于频繁，请稍后再试"
                )

            if current == 0:
                # 首次请求，设置窗口过期时间
                await redis.setex(key, window, 1)
            else:
                # 非首次请求，自增计数
                await redis.incr(key)
        except HTTPException:
            raise
        except Exception:
            # Redis 异常时降级放行，不影响正常请求
            pass

    return dependency
