"""
组织管理 API 路由 —— 创建/更新/删除组织、邀请/移除成员、修改角色。
"""
from fastapi import APIRouter, Depends, Path, Query, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, delete, text, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.organization import Organization, OrganizationMember
from app.models.space import Space
from app.utils.auth_utils import get_current_user_id, get_current_user_info
from app.db.db_config import get_db
from app.core.success_response import success_response
from app.core.exceptions import OrganizationNotFoundException
from app.core.permission import require_role, _get_user_role
from app.core.audit import write_audit_log

org_router = APIRouter(prefix="/org", tags=["organization"])


# ---------- 请求模型 ----------

class OrgCreate(BaseModel):
    """创建组织的请求体。"""
    name: str
    description: str = ""


class OrgUpdate(BaseModel):
    """更新组织信息的请求体，所有字段可选。"""
    name: Optional[str] = None
    description: Optional[str] = None


class InviteRequest(BaseModel):
    """邀请成员的请求体，username 和 email 至少填一个。"""
    username: str
    email: Optional[str] = None


class RoleUpdateRequest(BaseModel):
    """修改成员角色的请求体。"""
    role: str


def _member_to_dict(member: OrganizationMember) -> dict:
    """
    将组织成员模型对象转换为字典格式。

    参数:
        member (OrganizationMember): 组织成员 ORM 对象。

    返回:
        dict: 包含 id、user_id、username、role、joined_at 的字典。
             username 为空时回退到 user_id。
    """
    return {
        "id": member.id,
        "user_id": member.user_id,
        "username": member.username or member.user_id,
        "role": member.role,
        "joined_at": str(member.joined_at) if member.joined_at else None,
    }


async def _lookup_user_for_invite(payload: InviteRequest) -> dict | None:
    """
    从 Django 用户数据库中查找被邀请的用户。

    项目历史上存在两种不同的物理表名（Django 模型的 user_service 和旧数据库的 user），
    因此会依次尝试两个表名进行查询。用户名不保证唯一，优先使用邮箱查找。

    参数:
        payload (InviteRequest): 邀请请求，包含 username 和可选的 email。

    返回:
        dict | None: 找到用户时返回包含 user_id、username、email 的字典；
                    未找到或参数为空时返回 None。

    异常:
        HTTPException: 用户名不唯一且未提供邮箱时抛出 400 错误。

    关键逻辑:
        - 通过环境变量获取 MySQL 连接信息，直连 Django 用户数据库。
        - 有 email 时按 email 精确匹配，无 email 时按 username 匹配。
        - 使用独立的数据库连接引擎，查询后立即释放。
    """
    import os

    identifier = (payload.email or payload.username or "").strip()
    if not identifier:
        return None

    mysql_user = os.getenv("MYSQL_USER", "root")
    mysql_pass = os.getenv("MYSQL_PASSWORD", "")
    mysql_host = os.getenv("MYSQL_HOST", "localhost")
    mysql_port = os.getenv("MYSQL_PORT", "3306")
    django_db_url = (
        f"mysql+aiomysql://{mysql_user}:{mysql_pass}@{mysql_host}:{mysql_port}/"
        "user_service?charset=utf8mb4"
    )

    where_clause = "email = :identifier" if payload.email else "(username = :identifier OR email = :identifier)"
    last_error: Exception | None = None
    engine = create_async_engine(django_db_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        async with engine.connect() as conn:
            for table_name in ("user_service", "user"):
                try:
                    result = await conn.execute(
                        text(
                            f"SELECT uuid, username, email FROM `{table_name}` "
                            f"WHERE {where_clause} LIMIT 2"
                        ),
                        {"identifier": identifier},
                    )
                    rows = result.mappings().all()
                except SQLAlchemyError as exc:
                    last_error = exc
                    continue

                if len(rows) > 1 and not payload.email:
                    raise HTTPException(status_code=400, detail="用户名不唯一，请使用邮箱邀请")
                if rows:
                    row = rows[0]
                    return {
                        "user_id": row["uuid"],
                        "username": row["username"] or identifier,
                        "email": row["email"],
                    }
    finally:
        await engine.dispose()

    if last_error is not None:
        from app.core.logger_handler import logger
        logger.warning(f"查询 Django 用户失败: {last_error}")
    return None


# ---------- 组织 CRUD ----------

@org_router.post("/create")
async def create_org(
    payload: OrgCreate,
    user_info: dict = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_db),
):
    """
    创建组织，创建者自动成为 owner。

    参数:
        payload (OrgCreate): 创建组织的请求体，包含 name 和 description。
        user_info (dict): 当前登录用户信息字典，包含 user_id 和 username。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含新创建的组织 id、org_id 和 name。

    异常:
        HTTPException: 组织名称为空时抛出 400 错误。
    """
    user_id = user_info["user_id"]
    username = user_info["username"]
    org_name = payload.name.strip()
    if not org_name:
        raise HTTPException(status_code=400, detail="组织名称不能为空")

    org = Organization(name=org_name, description=payload.description or "", owner_id=user_id)
    db.add(org)
    await db.flush()

    member = OrganizationMember(org_id=org.id, user_id=user_id, username=username, role="owner")
    db.add(member)
    await db.flush()

    await write_audit_log(db, user_id, "create", "org", org.id, org_id=org.id, detail={"name": org_name})
    await db.commit()

    return success_response(message="组织创建成功", data={"id": org.id, "org_id": org.id, "name": org.name})


@org_router.get("/list")
async def list_orgs(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前用户所属的所有组织列表。

    关联查询组织表和成员表，返回每个组织的基本信息、用户在该组织中的角色以及成员数量。
    按组织创建时间倒序排列。

    参数:
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 orgs 列表和 total 数量。
    """
    stmt = (
        select(Organization, OrganizationMember.role)
        .join(OrganizationMember, Organization.id == OrganizationMember.org_id)
        .where(OrganizationMember.user_id == user_id)
        .order_by(Organization.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    orgs = []
    for org, role in rows:
        count_stmt = select(func.count(OrganizationMember.id)).where(OrganizationMember.org_id == org.id)
        count_result = await db.execute(count_stmt)
        member_count = count_result.scalar() or 0

        orgs.append({
            "id": org.id,
            "org_id": org.id,
            "name": org.name,
            "description": org.description,
            "owner_id": org.owner_id,
            "role": role,
            "member_count": member_count,
            "created_at": str(org.created_at) if org.created_at else None,
        })

    return success_response(data={"orgs": orgs, "total": len(orgs)})


@org_router.get("/{org_id}")
async def get_org(
    org_id: str = Path(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取组织详情，包括成员列表。

    仅组织成员可以查看，返回组织基本信息和所有成员的列表。

    参数:
        org_id (str): 组织 ID，从路径参数获取。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含组织详情和 members 列表。

    异常:
        HTTPException: 用户不是该组织成员时抛出 403。
        OrganizationNotFoundException: 组织不存在时抛出。
    """
    role = await _get_user_role(db, org_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="您不是该组织的成员")

    stmt = select(Organization).where(Organization.id == org_id)
    result = await db.execute(stmt)
    org = result.scalar_one_or_none()
    if not org:
        raise OrganizationNotFoundException()

    # 成员列表
    member_stmt = select(OrganizationMember).where(OrganizationMember.org_id == org_id)
    member_result = await db.execute(member_stmt)
    members = [_member_to_dict(m) for m in member_result.scalars().all()]

    return success_response(data={
        "id": org.id,
        "org_id": org.id,
        "name": org.name,
        "description": org.description,
        "owner_id": org.owner_id,
        "current_user_role": role,
        "members": members,
        "member_count": len(members),
        "created_at": str(org.created_at) if org.created_at else None,
        "updated_at": str(org.updated_at) if org.updated_at else None,
    })


@org_router.put("/{org_id}")
async def update_org(
    org_id: str,
    payload: OrgUpdate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_role(["owner", "admin"])),
):
    """
    更新组织名称和/或描述。

    仅 owner 或 admin 角色可执行。只更新请求中非 None 的字段。

    参数:
        org_id (str): 组织 ID。
        payload (OrgUpdate): 更新请求体，name 和 description 均可选。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，message 为"组织更新成功"。

    异常:
        HTTPException: 组织名称为空时抛出 400。
        OrganizationNotFoundException: 组织不存在时抛出。
    """
    stmt = select(Organization).where(Organization.id == org_id)
    result = await db.execute(stmt)
    org = result.scalar_one_or_none()
    if not org:
        raise OrganizationNotFoundException()

    if payload.name is not None:
        org_name = payload.name.strip()
        if not org_name:
            raise HTTPException(status_code=400, detail="组织名称不能为空")
        org.name = org_name
    if payload.description is not None:
        org.description = payload.description

    await write_audit_log(db, user_id, "update", "org", org_id, org_id=org_id,
                          detail={"name": org.name})
    await db.commit()

    return success_response(message="组织更新成功")


@org_router.delete("/{org_id}")
async def delete_org(
    org_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_role(["owner"])),
):
    """
    删除组织及其所有子资源。

    仅 owner 可执行。按顺序删除：空间 -> 成员 -> 组织本身。

    参数:
        org_id (str): 组织 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，message 为"组织已删除"。

    异常:
        OrganizationNotFoundException: 组织不存在时抛出。

    关键逻辑:
        显式删除子资源（Space、OrganizationMember）是为了兼容已有表结构，
        虽然外键 CASCADE 也能处理，但显式删除更安全可靠。
    """
    stmt = select(Organization).where(Organization.id == org_id)
    result = await db.execute(stmt)
    org = result.scalar_one_or_none()
    if not org:
        raise OrganizationNotFoundException()

    # 先删子资源和成员，再删组织（外键 CASCADE 会处理，显式删除便于兼容已有表结构）
    await db.execute(delete(Space).where(Space.org_id == org_id))
    await db.execute(delete(OrganizationMember).where(OrganizationMember.org_id == org_id))
    await db.execute(delete(Organization).where(Organization.id == org_id))

    await write_audit_log(db, user_id, "delete", "org", org_id, org_id=org_id, detail={"name": org.name})
    await db.commit()

    return success_response(message="组织已删除")


# ---------- 成员管理 ----------

@org_router.get("/{org_id}/members")
async def list_members(
    org_id: str = Path(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定组织的成员列表。

    仅组织成员可查看。

    参数:
        org_id (str): 组织 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 members 列表和 total 数量。

    异常:
        HTTPException: 用户不是该组织成员时抛出 403。
    """
    role = await _get_user_role(db, org_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="您不是该组织的成员")

    stmt = select(OrganizationMember).where(OrganizationMember.org_id == org_id)
    result = await db.execute(stmt)
    members = [_member_to_dict(m) for m in result.scalars().all()]
    return success_response(data={"members": members, "total": len(members)})


@org_router.post("/{org_id}/invite")
async def invite_member(
    org_id: str,
    payload: InviteRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    邀请用户加入组织。

    通过用户名或邮箱查找目标用户，检查权限和重复成员后添加为 member 角色。
    仅 owner 或 admin 可执行。

    参数:
        org_id (str): 组织 ID。
        payload (InviteRequest): 邀请请求体，包含 username 和可选 email。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含被邀请用户的 user_id 和 username。

    异常:
        HTTPException: 用户名和邮箱都为空时抛出 400。
        HTTPException: 组织不存在时抛出 OrganizationNotFoundException。
        HTTPException: 权限不足时抛出 403。
        HTTPException: 目标用户不存在时抛出 404。
        HTTPException: 不能邀请自己时抛出 400。
        HTTPException: 用户已是成员时抛出 400。

    关键逻辑:
        - 通过 _lookup_user_for_invite 从 Django 用户数据库查找目标用户。
        - 检查是否为重复邀请或邀请自己。
        - 记录审计日志。
    """
    from app.core.logger_handler import logger
    payload.username = (payload.username or "").strip()
    payload.email = (payload.email or "").strip() or None
    if not payload.username and not payload.email:
        raise HTTPException(status_code=400, detail="请输入用户名或邮箱")
    logger.info(f"【组织】邀请成员: org_id={org_id}, user_id={user_id}, username={payload.username}, email={payload.email}")

    # 检查组织是否存在
    org_stmt = select(Organization).where(Organization.id == org_id)
    org_result = await db.execute(org_stmt)
    if not org_result.scalar_one_or_none():
        raise OrganizationNotFoundException()

    # 手动校验权限
    role = await _get_user_role(db, org_id, user_id)
    logger.info(f"【组织】当前用户角色: {role}")
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="权限不足，需要 owner 或 admin 角色")

    target_user = await _lookup_user_for_invite(payload)
    if not target_user:
        raise HTTPException(status_code=404, detail=f"用户 '{payload.email or payload.username}' 不存在")

    target_user_id = target_user["user_id"]
    target_username = target_user["username"]

    # 不能邀请自己
    if target_user_id == user_id:
        raise HTTPException(status_code=400, detail="不能邀请自己")

    # 检查是否已是成员
    existing = await _get_user_role(db, org_id, target_user_id)
    if existing:
        raise HTTPException(status_code=400, detail="该用户已是组织成员")

    member = OrganizationMember(org_id=org_id, user_id=target_user_id, username=target_username, role="member")
    db.add(member)

    await write_audit_log(db, user_id, "invite", "member", target_user_id,
                          org_id=org_id, detail={"invited_user": target_username, "email": target_user.get("email")})
    await db.commit()

    logger.info(f"【组织】成员邀请成功: {target_username}")
    return success_response(message="成员邀请成功", data={"user_id": target_user_id, "username": target_username})


@org_router.delete("/{org_id}/member/{target_user_id}")
async def remove_member(
    org_id: str,
    target_user_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_role(["owner", "admin"])),
):
    """
    从组织中移除指定成员。

    仅 owner 或 admin 可执行，不能移除 owner 角色的成员。

    参数:
        org_id (str): 组织 ID。
        target_user_id (str): 要移除的目标用户 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，message 为"成员已移除"。

    异常:
        HTTPException: 目标用户不是成员时抛出 404。
        HTTPException: 尝试移除 owner 时抛出 400。
    """
    # 不能移除 owner
    target_role = await _get_user_role(db, org_id, target_user_id)
    if target_role is None:
        raise HTTPException(status_code=404, detail="该用户不是组织成员")
    if target_role == "owner":
        raise HTTPException(status_code=400, detail="不能移除组织所有者")

    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == target_user_id,
        )
    )

    await write_audit_log(db, user_id, "delete", "member", target_user_id,
                          org_id=org_id, detail={"removed_user": target_user_id})
    await db.commit()

    return success_response(message="成员已移除")


@org_router.put("/{org_id}/member/{target_user_id}/role")
async def update_member_role(
    org_id: str,
    target_user_id: str,
    payload: RoleUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_role(["owner"])),
):
    """
    修改组织成员的角色。

    仅 owner 可执行。角色只能设为 admin 或 member，不能设为 owner（所有者转移需单独流程）。
    不能修改 owner 本身的角色。

    参数:
        org_id (str): 组织 ID。
        target_user_id (str): 要修改角色的目标用户 ID。
        payload (RoleUpdateRequest): 角色更新请求体，role 必须是 owner/admin/member。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，message 包含更新后的角色名。

    异常:
        HTTPException: 角色值不在允许范围内时抛出 400。
        HTTPException: 尝试将角色设为 owner 时抛出 400。
        HTTPException: 目标用户不是成员时抛出 404。
        HTTPException: 尝试修改 owner 角色时抛出 400。
    """
    if payload.role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="角色必须是 owner/admin/member")
    if payload.role == "owner":
        raise HTTPException(status_code=400, detail="不能通过角色修改转移 owner，请先实现所有者转移流程")

    stmt = select(OrganizationMember).where(
        OrganizationMember.org_id == org_id,
        OrganizationMember.user_id == target_user_id,
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="该用户不是组织成员")
    if member.role == "owner":
        raise HTTPException(status_code=400, detail="不能修改组织所有者角色")

    old_role = member.role
    member.role = payload.role

    await write_audit_log(db, user_id, "update", "member", target_user_id,
                          org_id=org_id, detail={"old_role": old_role, "new_role": payload.role})
    await db.commit()

    return success_response(message=f"角色已更新为 {payload.role}")
