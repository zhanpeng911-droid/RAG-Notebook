"""
基于 Redis 的限流工具 —— 提供路由级限流依赖函数。

使用 Redis 的 INCR + EXPIRE 实现固定窗口限流。
优先按 bearer token 分桶，匿名请求再回退到 IP，避免 NAT 场景互相误伤。
当 Redis 不可用时自动降级放行（不影响正常请求）。

用法:
    @router.get("/some-endpoint")
    async def endpoint(_: None = Depends(rate_limit(limit=10, window=60))):
        ...
"""
import hashlib
import os

from fastapi import Request, HTTPException

from app.db.redis_config import connect_redis, is_redis_available


# .env 文件读取结果的进程级缓存：避免每个请求都读盘解析 .env
_UNSET = object()
_env_file_flag_cache = _UNSET


def _read_rate_limit_flag_from_env_file() -> bool:
    """从 backend/.env 文件读取 RATE_LIMIT_ENABLED（读不到时默认开启）。"""
    try:
        from pathlib import Path
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("RATE_LIMIT_ENABLED"):
                    return line.split("=", 1)[1].strip().lower() == "true"
    except Exception:
        pass
    return True


def _is_rate_limit_enabled() -> bool:
    """
    动态读取限流开关。

    优先读系统环境变量（测试/CI 可动态设置）；
    未设置时读 backend/.env，读取结果进程内缓存（配置文件不会在运行中变化，
    避免每个请求都解析一次 .env 文件）。
    """
    # 1. 先读系统环境变量（测试时可手动设置，保持动态）
    val = os.getenv("RATE_LIMIT_ENABLED")
    if val is not None:
        return val.lower() == "true"
    # 2. fallback：读 .env 文件（结果缓存，只读一次盘）
    global _env_file_flag_cache
    if _env_file_flag_cache is _UNSET:
        _env_file_flag_cache = _read_rate_limit_flag_from_env_file()
    return bool(_env_file_flag_cache)


def _build_rate_limit_key(request: Request) -> str:
    """按路由 + 认证主体分桶，匿名请求回退到客户端 IP。"""
    route_key = request.url.path.strip("/").replace("/", ":") or "root"

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            return f"rate_limit:{route_key}:token:{token_hash}"

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else ""
    if not client_ip and request.client:
        client_ip = request.client.host
    client_ip = client_ip or "unknown"
    return f"rate_limit:{route_key}:ip:{client_ip}"


def rate_limit(limit: int = 1, window: int = 60):
    """
    限流依赖函数 —— 用于 FastAPI 的 Depends()。

    原理：基于 Redis 的固定窗口计数器。
    - 每个客户端身份（token 或 IP）+ 路由对应一个 Redis key
    - 每次请求原子 INCR 自增计数
    - 首次请求（或 key 丢失 TTL 时）设置窗口过期时间
    - 超过 limit 则返回 429

    :param limit: 时间窗口内的最大请求数
    :param window: 时间窗口大小（秒）
    :return: FastAPI 依赖函数
    """
    async def dependency(request: Request):
        # 开关关闭或 Redis 不可用时直接放行
        if not _is_rate_limit_enabled() or not is_redis_available():
            return

        key = _build_rate_limit_key(request)

        try:
            redis = await connect_redis()
            if redis is None:
                return

            # 原子计数：先 INCR 再判断，避免"先 GET 再 SET"在并发下互相覆盖计数
            current = await redis.incr(key)

            # 首次请求（或 key 意外丢失 TTL 时）设置窗口过期时间
            if current == 1 or await redis.ttl(key) == -1:
                await redis.expire(key, window)

            if current > limit:
                raise HTTPException(
                    status_code=429,
                    detail="请求过于频繁，请稍后再试"
                )
        except HTTPException:
            raise
        except Exception:
            # Redis 异常时降级放行，不影响正常请求
            pass

    return dependency
