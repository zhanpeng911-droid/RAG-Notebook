from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.core.success_response import success_response
from app.utils.auth_utils import get_current_user_id, get_user_info_from_redis, security

user_router = APIRouter(tags=["user"], prefix="/user")

@user_router.get("/detail/")
async def get_user_info(user_id: str = Depends(get_current_user_id), credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    获取当前登录用户的详细信息。

    通过 JWT token 解析出用户 ID，然后从 Redis 缓存中查询用户信息。
    若 Redis 中无缓存，会回退到数据库查询并更新缓存。

    参数:
        user_id (str): 当前登录用户 ID，由 JWT 认证依赖注入。
        credentials (HTTPAuthorizationCredentials): HTTP Authorization 凭据，
            用于在 Redis 缓存未命中时从数据库获取用户信息。

    返回:
        成功响应，data 包含用户详细信息字典。
    """
    # 借助 uuid 去查询redis 中存储的用户信息
    user_info = await get_user_info_from_redis(user_id, credentials)
    return success_response(
        message="获取用户信息成功",
        data=user_info,
    )