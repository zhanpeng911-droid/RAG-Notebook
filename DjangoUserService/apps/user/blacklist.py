"""
JWT 撤销黑名单 —— 共享 Redis，失败关闭（fail-closed）。

撤销是安全操作，不能静默降级：
- 生产/配置了共享 Redis 时：Redis 不可用 → 抛 RevocationUnavailable，调用方返回 503，
  不能静默放行可能已被撤销的 token。
- 未配置共享 Redis（开发/测试显式 LocMem）：走 Django cache（进程内）。
  生产环境由 settings.py 强制要求 REDIS_CACHE_URL，不会走到该分支。

键格式（与 FastAPI backend 对齐，见 docs/JWT_CONTRACT.md）：
- 新写入：`blacklist:{jti}`
- 过渡期兼容读取：同时读 `blacklist:{jti}` 与 `:1:blacklist:{jti}`
  （旧版本经 Django cache 前缀写入的键）
"""
import logging
import time

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

BLACKLIST_PREFIX = "blacklist:"
LEGACY_PREFIX = ":1:blacklist:"


class RevocationUnavailable(Exception):
    """撤销服务（Redis）不可用 —— 调用方应返回 503，不得伪报成功。"""


def _use_shared_redis() -> bool:
    return bool(settings.REDIS_CACHE_URL)


def _client() -> redis.Redis:
    url = settings.REDIS_CACHE_URL
    if not url:
        raise RevocationUnavailable("撤销服务不可用（未配置共享 Redis）")
    return redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
    )


def _django_cache_is_blacklisted(jti: str) -> bool:
    from django.core.cache import cache

    return bool(
        cache.get(f"{BLACKLIST_PREFIX}{jti}")
        or cache.get(f"{LEGACY_PREFIX}{jti}")
    )


def is_blacklisted(jti: str | None) -> bool:
    """检查 jti 是否已被撤销。

    共享 Redis 不可用时抛 RevocationUnavailable（失败关闭）；
    开发/测试未配置共享 Redis 时走 Django cache（进程内）。
    """
    if not jti:
        return False
    if not _use_shared_redis():
        return _django_cache_is_blacklisted(jti)
    try:
        client = _client()
        try:
            return bool(
                client.exists(f"{BLACKLIST_PREFIX}{jti}", f"{LEGACY_PREFIX}{jti}")
            )
        finally:
            client.close()
    except RevocationUnavailable:
        raise
    except Exception as e:
        logger.error("撤销检查失败（Redis 不可用）: %s", e)
        raise RevocationUnavailable("撤销服务不可用") from e


def blacklist_token(token: str) -> bool:
    """将 token 的 jti 写入黑名单。

    返回 True 表示已写入并确认；False 表示无需撤销（无 jti/无法解码）。
    共享 Redis 不可用时抛 RevocationUnavailable，调用方必须返回 503，
    不得伪报"已撤销成功"。
    """
    try:
        import jwt as pyjwt

        payload = pyjwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except Exception:
        # 无法解码/无 jti 的 token 无需撤销，视为本机无需处理
        return False

    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti:
        return False

    ttl = max(int(exp) - int(time.time()), 0) if exp else 0

    if not _use_shared_redis():
        # 开发/测试显式 LocMem：进程内写读
        from django.core.cache import cache

        cache.set(f"{BLACKLIST_PREFIX}{jti}", "1", ttl)
        return True

    try:
        client = _client()
        try:
            client.set(f"{BLACKLIST_PREFIX}{jti}", "1", ex=ttl)
            if not client.exists(f"{BLACKLIST_PREFIX}{jti}"):
                raise RevocationUnavailable("撤销写入未确认")
        finally:
            client.close()
    except RevocationUnavailable:
        raise
    except Exception as e:
        logger.error("黑名单写入失败（撤销不可用）: %s", e)
        raise RevocationUnavailable("撤销服务不可用") from e
    return True
