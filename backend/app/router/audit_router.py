"""
审计日志 API 路由 —— 查询日志、操作统计。
"""
from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.organization import OrganizationMember
from app.utils.auth_utils import get_current_user_id
from app.db.db_config import get_db
from app.core.success_response import success_response

audit_router = APIRouter(prefix="/audit", tags=["audit"])


async def _get_member_org_ids(db: AsyncSession, user_id: str) -> list[str]:
    """
    获取用户所属的所有组织 ID 列表（不限角色）。

    参数:
        db (AsyncSession): 异步数据库会话。
        user_id (str): 用户 ID。

    返回:
        list[str]: 用户所属的组织 ID 列表。
    """
    result = await db.execute(
        select(OrganizationMember.org_id).where(OrganizationMember.user_id == user_id)
    )
    return list(result.scalars().all())


async def _get_audit_org_ids(db: AsyncSession, user_id: str) -> list[str]:
    """
    获取用户拥有审计日志查看权限的组织 ID 列表。

    仅返回用户角色为 owner 或 admin 的组织，因为只有这两种角色可以查看审计日志。

    参数:
        db (AsyncSession): 异步数据库会话。
        user_id (str): 用户 ID。

    返回:
        list[str]: 用户有权限查看审计日志的组织 ID 列表。
    """
    result = await db.execute(
        select(OrganizationMember.org_id).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.role.in_(["owner", "admin"]),
        )
    )
    return list(result.scalars().all())


@audit_router.get("/logs")
async def get_audit_logs(
    org_id: str = Query(None, description="组织ID（可选）"),
    action: str = Query(None, description="操作类型筛选"),
    keyword: str = Query(None, description="关键词筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    分页查询审计日志。

    仅组织的 owner 或 admin 角色可以查看审计日志。支持按组织 ID、操作类型和关键词筛选。

    参数:
        org_id (str, optional): 组织 ID，不指定则查询用户有权限的所有组织。
        action (str, optional): 操作类型筛选，如 create、update、delete 等。
        keyword (str, optional): 关键词模糊搜索，匹配 user_id、action、resource_type、resource_id 和 detail。
        page (int): 页码，从 1 开始，默认 1。
        page_size (int): 每页数量，1~100，默认 20。
        user_id (str): 当前登录用户 ID，由认证依赖注入。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 logs 列表、total 总数、page 和 page_size。

    异常:
        HTTPException: 用户不是任何组织的 owner/admin 时抛出 403。
    """
    audit_org_ids = await _get_audit_org_ids(db, user_id)
    conditions = []
    if org_id:
        if org_id not in audit_org_ids:
            raise HTTPException(status_code=403, detail="权限不足，仅组织拥有者或管理员可查看审计日志")
        conditions.append(AuditLog.org_id == org_id)
    elif audit_org_ids:
        conditions.append(AuditLog.org_id.in_(audit_org_ids))
    else:
        raise HTTPException(status_code=403, detail="权限不足，仅组织拥有者或管理员可查看审计日志")

    if action:
        conditions.append(AuditLog.action == action)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(
            AuditLog.user_id.like(like),
            AuditLog.action.like(like),
            AuditLog.resource_type.like(like),
            AuditLog.resource_id.like(like),
            cast(AuditLog.detail, String).like(like),
        ))

    # 总数
    count_stmt = select(func.count(AuditLog.id)).where(*conditions)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # 分页查询
    stmt = select(AuditLog).where(*conditions)
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    logs = [
        {
            "id": log.id,
            "org_id": log.org_id,
            "user_id": log.user_id,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": str(log.created_at) if log.created_at else None,
        }
        for log in result.scalars().all()
    ]

    return success_response(data={"logs": logs, "total": total, "page": page, "page_size": page_size})


@audit_router.get("/stats")
async def get_audit_stats(
    org_id: str = Query(..., description="组织ID"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定组织的审计操作统计。

    按操作类型（action）分组计数，返回每种操作的执行次数。

    参数:
        org_id (str): 组织 ID，必填。
        user_id (str): 当前登录用户 ID，由认证依赖注入。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 org_id 和 stats 字典（key 为操作类型，value 为次数）。

    异常:
        HTTPException: 用户不是该组织的 owner/admin 时抛出 403。
    """
    audit_org_ids = await _get_audit_org_ids(db, user_id)
    if org_id not in audit_org_ids:
        raise HTTPException(status_code=403, detail="权限不足，仅组织拥有者或管理员可查看审计日志")

    stmt = (
        select(AuditLog.action, func.count(AuditLog.id).label("count"))
        .where(AuditLog.org_id == org_id)
        .group_by(AuditLog.action)
    )
    result = await db.execute(stmt)
    stats = {row.action: row.count for row in result.all()}

    return success_response(data={"org_id": org_id, "stats": stats})
