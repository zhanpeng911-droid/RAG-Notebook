"""
权限检查中间件 —— require_role 依赖，检查当前用户在组织中的角色。

角色层级：
- owner: 组织所有者（最高权限）
- admin: 管理员
- member: 普通成员

用法:
    @router.post("/org/{org_id}/invite")
    async def invite(
        org_id: str,
        _: None = Depends(require_role(["owner", "admin"])),
    ):
        ...
"""
from fastapi import Depends, Query, Path, HTTPException

from app.utils.auth_utils import get_current_user_id
from app.db.db_config import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.organization import OrganizationMember


async def _get_user_role(db: AsyncSession, org_id: str, user_id: str) -> str | None:
    """
    查询用户在指定组织中的角色。

    :return: 角色字符串（owner/admin/member），不存在返回 None
    """
    stmt = select(OrganizationMember).where(
        OrganizationMember.org_id == org_id,
        OrganizationMember.user_id == user_id,
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    return member.role if member else None


def require_role(allowed_roles: list[str]):
    """
    创建 FastAPI 依赖 —— 检查当前用户在指定组织中的角色。

    :param allowed_roles: 允许的角色列表
    :return: FastAPI 依赖函数
    """
    async def _check_role(
        org_id: str = Path(..., description="组织ID"),
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ):
        role = await _get_user_role(db, org_id, user_id)
        if role is None:
            raise HTTPException(status_code=403, detail="您不是该组织的成员")
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail=f"权限不足，需要角色: {', '.join(allowed_roles)}，当前角色: {role}")
        return role

    return _check_role
