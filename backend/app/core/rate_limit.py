"""
基于 Redis 的限流工具 —— 提供路由级限流依赖函数。

- 认证请求按**已验证 user_id + 路由模板**分桶（不按完整 token、不按实际资源路径），
  换 token / 换 note ID 不会重新获得一份同类操作额度。
- 匿名只使用经可信代理配置确认的客户端 IP：`TRUSTED_PROXY_IPS` 为空时不信任
  任意 `X-Forwarded-For`，只使用直接对端。
- 计数与 TTL 合并为原子操作（Redis Lua 固定窗口），避免两条命令之间进程中断丢 TTL。
- `fail_closed=True`（高成本生成/重索引）在限流存储不可用时默认 503；
  便宜读取按明示策略降级放行。

用法:
    @router.get("/some-endpoint")
    async def endpoint(_: None = Depends(rate_limit(limit=10, window=60))):
        ...
"""
import os

from fastapi import Request, HTTPException

from app.db.redis_config import connect_redis

# 原子 INCR + 首次/丢失 TTL 时 EXPIRE（固定窗口）：单脚本原子执行，
# 进程在两条命令之间中断不会产生无 TTL 的孤儿 key
_INCR_TTL_LUA = """
local c = redis.call('INCR', KEYS[1])
local t = redis.call('TTL', KEYS[1])
if c == 1 or t == -1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return c
"""

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
    """动态读取限流开关（系统环境变量优先，否则读 .env 并缓存）。"""
    val = os.getenv("RATE_LIMIT_ENABLED")
    if val is not None:
        return val.lower() == "true"
    global _env_file_flag_cache
    if _env_file_flag_cache is _UNSET:
        _env_file_flag_cache = _read_rate_limit_flag_from_env_file()
    return bool(_env_file_flag_cache)


def _verified_user_id(request: Request) -> str | None:
    """解析已验证身份的 user_id（签名/过期已验证），无效返回 None。

    仅用于限流分桶；完整认证（含黑名单）仍由路由的认证依赖执行。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        from app.utils.auth_utils import decode_django_jwt

        payload = decode_django_jwt(token)  # 签名与过期已验证
        return payload.get("user_id") if payload else None
    except Exception:
        return None


def _trusted_client_ip(request: Request) -> str:
    """只有直接对端在 TRUSTED_PROXY_IPS 内才信任 X-Forwarded-For；否则用直接对端。"""
    peer = request.client.host if request.client else ""
    trusted = os.getenv("TRUSTED_PROXY_IPS", "")
    if peer and trusted:
        trusted_set = {v.strip() for v in trusted.split(",") if v.strip()}
        if peer in trusted_set:
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
    return peer or "unknown"


def _route_template(request: Request) -> str:
    """路由模板（含参数占位符），避免按实际资源路径分桶。"""
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return path.strip("/").replace("/", ":") or "root"


def _build_rate_limit_key(request: Request, user_id: str | None) -> str:
    route_key = _route_template(request)
    if user_id:
        return f"rate_limit:{route_key}:user:{user_id}"
    return f"rate_limit:{route_key}:ip:{_trusted_client_ip(request)}"


async def _incr_with_ttl(client, key: str, window: int) -> int:
    """原子 INCR + 首次 EXPIRE；eval 失败回退两条命令路径（尽力）。"""
    try:
        return int(await client.eval(_INCR_TTL_LUA, 1, key, window))
    except Exception:
        current = await client.incr(key)
        if current == 1 or await client.ttl(key) == -1:
            await client.expire(key, window)
        return current


def rate_limit(limit: int = 1, window: int = 60, *, fail_closed: bool = False):
    """
    限流依赖函数 —— 用于 FastAPI 的 Depends()。

    :param limit: 时间窗口内的最大请求数
    :param window: 时间窗口大小（秒）
    :param fail_closed: 高成本接口在限流存储不可用时默认 503（否则降级放行）
    """
    async def dependency(request: Request):
        if not _is_rate_limit_enabled():
            return

        user_id = _verified_user_id(request)
        key = _build_rate_limit_key(request, user_id)

        try:
            client = await connect_redis()
            if client is None:
                if fail_closed:
                    raise HTTPException(
                        status_code=503, detail="限流服务不可用，请稍后再试"
                    )
                return

            current = await _incr_with_ttl(client, key, window)
            if current > limit:
                raise HTTPException(
                    status_code=429,
                    detail="请求过于频繁，请稍后再试",
                    headers={"Retry-After": str(window)},
                )
        except HTTPException:
            raise
        except Exception:
            if fail_closed:
                raise HTTPException(
                    status_code=503, detail="限流服务不可用，请稍后再试"
                )

    return dependency
