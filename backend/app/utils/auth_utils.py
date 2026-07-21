import json
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

    # 检查JWT是否在黑名单中。无法确认撤销状态时拒绝请求，避免撤销 token 被继续使用。
    jti = payload.get("jti")
    if jti and JWT_BLACKLIST_CHECK_ENABLED:
        try:
            import asyncio
            async def _check_blacklist():
                if JWT_BLACKLIST_REDIS_URL:
                    client = redis.from_url(
                        JWT_BLACKLIST_REDIS_URL,
                        decode_responses=True,
                        socket_connect_timeout=0.3,
                        socket_timeout=0.3,
                    )
                    try:
                        return await client.exists(f"blacklist:{jti}", f":1:blacklist:{jti}")
                    finally:
                        await client.aclose()

                client = await connect_redis()
                if client is None:
                    raise RuntimeError("Redis unavailable")
                return await client.exists(f"blacklist:{jti}", f":1:blacklist:{jti}")
            is_blacklisted = await asyncio.wait_for(_check_blacklist(), timeout=0.3)
            if is_blacklisted:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not verify token revocation status",
            )

    # 从Django JWT中提取user_id（uuid）
    user_id: str = payload.get("user_id")
    
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
