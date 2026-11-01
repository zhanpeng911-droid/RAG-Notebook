import asyncio
import json
import time
from typing import Optional, Dict, Any
import requests
import redis.asyncio as redis
from jose import JWTError, jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.logger_handler import logger
from app.db.redis_config import connect_redis, set_redis_cache
from app.config.validator import get_settings

_settings = get_settings()

# Django JWT 配置
SECRET_KEY = _settings.SECRET_KEY
ALGORITHM = _settings.ALGORITHM

# JWT 黑名单检查开关
JWT_BLACKLIST_CHECK_ENABLED = _settings.JWT_BLACKLIST_CHECK_ENABLED
JWT_BLACKLIST_REDIS_URL = _settings.jwt_blacklist_redis_url

# FastAPI Bearer 认证方案 —— 用于 Depends() 依赖注入
security = HTTPBearer()


# JWT 契约（与 DjangoUserService 对齐）：
# - algorithm: HS256
# - secret: backend SECRET_KEY == Django JWT_SECRET_KEY / settings.SECRET_KEY
# - claims: user_id, username, email?, exp, iat, jti
# - blacklist redis key: blacklist:{jti}（Django cache 可能带 :1: 前缀）
# - clock skew leeway: 30s
JWT_CLOCK_SKEW_LEEWAY = 30


def decode_django_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    解析 Django 生成的 JWT token。

    JWT payload 包含：
    - user_id: 用户 UUID
    - username: 用户名
    - email: 可选
    - exp / iat: 过期与签发时间
    - jti: JWT ID（用于黑名单检查）

    :param token: JWT token 字符串
    :return: payload 字典，解析失败返回 None
    """
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={
                "verify_exp": True,
                "leeway": JWT_CLOCK_SKEW_LEEWAY,
            },
        )
        return payload
    except JWTError:
        return None


_blacklist_client = None
_blacklist_client_url = None
_blacklist_lock = asyncio.Lock()
_blacklist_cache: dict[str, tuple[float, bool]] = {}
_blacklist_cache_url = None
_BLACKLIST_CACHE_TTL = 5.0
_BLACKLIST_CACHE_MAX = 10000


async def _get_blacklist_client():
    """Reuse one pooled client; per-request connection setup timed out at 50 QPS."""
    global _blacklist_client, _blacklist_client_url
    if not JWT_BLACKLIST_REDIS_URL:
        return await connect_redis()
    if _blacklist_client is not None and _blacklist_client_url == JWT_BLACKLIST_REDIS_URL:
        return _blacklist_client
    async with _blacklist_lock:
        if _blacklist_client is not None and _blacklist_client_url != JWT_BLACKLIST_REDIS_URL:
            try:
                await _blacklist_client.aclose()
            except Exception:
                pass
            _blacklist_client = None
        if _blacklist_client is None:
            _blacklist_client = redis.from_url(
                JWT_BLACKLIST_REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            _blacklist_client_url = JWT_BLACKLIST_REDIS_URL
        return _blacklist_client


async def _reject_if_blacklisted(payload: Dict[str, Any]) -> None:
    """
    检查 JWT 是否在黑名单中（登出/刷新撤销）。

    所有从 token 提取用户身份的依赖必须经过此检查，否则已撤销的 token 仍可访问接口。
    无法确认撤销状态时拒绝请求，避免撤销 token 被继续使用。
    """
    jti = payload.get("jti")
    if not (jti and JWT_BLACKLIST_CHECK_ENABLED):
        return

    try:
        async def _check_blacklist():
            global _blacklist_cache_url
            if _blacklist_cache_url != JWT_BLACKLIST_REDIS_URL:
                # A different backend/URL must not reuse another store's cache.
                _blacklist_cache.clear()
                _blacklist_cache_url = JWT_BLACKLIST_REDIS_URL
            now = time.monotonic()
            cached = _blacklist_cache.get(jti)
            # Cache only POSITIVE (revoked) results. Revocation is monotonic
            # for a JWT lifetime, while caching "not revoked" would let a token
            # revoked immediately after the check keep working for the TTL.
            if cached is not None and now - cached[0] < _BLACKLIST_CACHE_TTL:
                return True
            client = await _get_blacklist_client()
            if client is None:
                raise RuntimeError("Redis unavailable")
            result = await client.exists(f"blacklist:{jti}", f":1:blacklist:{jti}")
            if result:
                if len(_blacklist_cache) >= _BLACKLIST_CACHE_MAX:
                    _blacklist_cache.clear()
                _blacklist_cache[jti] = (now, True)
            else:
                _blacklist_cache.pop(jti, None)
            return bool(result)

        is_blacklisted = await asyncio.wait_for(_check_blacklist(), timeout=1.0)
        if is_blacklisted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise
    except Exception:
        # Drop a client bound to a dead connection/loop so the next request
        # can reconnect instead of failing forever.
        global _blacklist_client, _blacklist_client_url
        if _blacklist_client is not None:
            try:
                await _blacklist_client.aclose()
            except Exception:
                pass
            _blacklist_client = None
            _blacklist_client_url = None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify token revocation status",
        )


async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    获取当前用户ID —— FastAPI 依赖函数。

    流程：
    1. 从请求头提取 Bearer token
    2. 解析 JWT 获取 user_id
    3. 检查 JWT 是否在黑名单中（Redis）
    4. 返回 user_id

    用法：
        @router.get("/some-endpoint")
        async def endpoint(user_id: str = Depends(get_current_user_id)):
            ...

    :return: 用户 UUID
    """
    token = credentials.credentials
    payload = decode_django_jwt(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查JWT是否在黑名单中
    await _reject_if_blacklisted(payload)

    # 从Django JWT中提取user_id（uuid）
    user_id = payload.get("user_id")

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not find user ID in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def get_current_user_info(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    获取当前用户信息 —— 返回 user_id 和 username。

    与 get_current_user_id 保持一致：同样执行 JWT 黑名单（撤销）检查。

    :return: {"user_id": "xxx", "username": "xxx"}
    """
    token = credentials.credentials
    payload = decode_django_jwt(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查JWT是否在黑名单中
    await _reject_if_blacklisted(payload)

    user_id = payload.get("user_id")
    username = payload.get("username", "")

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not find user ID in token",
        )

    return {"user_id": user_id, "username": username}


async def fetch_user_info_from_django_api(token: str, url: str) -> Optional[Dict[str, Any]]:
    """
    调用 Django API 获取用户信息 —— 用于 JWT 中缺少用户信息时的降级方案。

    :param token: JWT token 字符串
    :param url: Django API 地址
    :return: 用户信息字典，失败返回 None
    """

    try:
        import asyncio
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        def _sync_get():
            return requests.get(url=url, headers=headers, timeout=5)

        response = await asyncio.to_thread(_sync_get)

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"从Django API获取用户信息失败, status={response.status_code}")
            return None
    except Exception as e:
        logger.error(f"调用Django API时出错: {e}")
        return None


async def get_user_info_from_redis(user_id: str, credentials: HTTPAuthorizationCredentials):
    """
    从 Redis 获取用户信息 —— 带降级策略。

    降级链：
    1. Redis 缓存命中 → 直接返回
    2. Redis 缓存未命中 → 调用 Django API → 写入 Redis 缓存
    3. Redis 不可用 → 直接调用 Django API

    :param user_id: 用户ID
    :param credentials: HTTP 认证凭据
    :return: 用户信息字典
    """
    key = f":1:user:{user_id}"

    try:
        client = await connect_redis()
        if client is None:
            return await fetch_user_info_from_django_api(
                credentials.credentials, _settings.DJANGO_API_URL + "/user/detail/"
            )

        user_info = await client.get(key)
        if user_info is not None:
            try:
                return json.loads(user_info)
            except (json.JSONDecodeError, TypeError):
                await client.delete(key)

        # Redis 没有缓存，降级到 Django API
        user_data = await fetch_user_info_from_django_api(
            credentials.credentials, _settings.DJANGO_API_URL + "/user/detail/"
        )
        if user_data:
            await set_redis_cache(key, user_data, expire=3600)
        return user_data

    except Exception:
        return await fetch_user_info_from_django_api(
            credentials.credentials, _settings.DJANGO_API_URL + "/user/detail/"
        )
