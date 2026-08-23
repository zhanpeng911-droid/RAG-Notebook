"""
运行时配置路由 —— 检索参数热更新接口。

- GET  /admin/runtime-config       查看全部参数（当前值/默认值/范围）
- PUT  /admin/runtime-config       批量更新参数
- POST /admin/runtime-config/reset 重置参数为默认值

权限说明：
当前为个人知识库应用（无全局管理员角色体系），修改需登录认证，
且所有变更写入审计日志（audit_log）保证可追溯。
生产部署如需收紧，可在此处叠加组织角色校验（require_role）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core import runtime_config
from app.core.audit import write_audit_log
from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.db.db_config import get_db
from app.utils.auth_utils import get_current_user_id
from app.config.validator import get_settings
from sqlalchemy.ext.asyncio import AsyncSession

runtime_config_router = APIRouter(prefix="/admin/runtime-config", tags=["admin"])


def _require_runtime_config_admin(user_id: str) -> None:
    """Restrict process-wide retrieval tuning to explicitly configured operators."""
    admin_ids = {
        value.strip()
        for value in get_settings().RUNTIME_CONFIG_ADMIN_USER_IDS.split(",")
        if value.strip()
    }
    if user_id not in admin_ids:
        raise HTTPException(status_code=403, detail="没有修改全局检索参数的权限")


class RuntimeConfigUpdateRequest(BaseModel):
    """批量更新请求：{values: {key: value}}"""
    values: dict


class RuntimeConfigResetRequest(BaseModel):
    """重置请求：keys 为空或 ["*"] 时重置全部"""
    keys: Optional[list[str]] = None


@runtime_config_router.get("")
async def get_runtime_configs(
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=30, window=60)),
):
    """查看全部运行时配置参数（含当前值、默认值与合法范围）"""
    return success_response(data={"params": runtime_config.get_all()})


@runtime_config_router.put("")
async def update_runtime_configs(
        request: RuntimeConfigUpdateRequest,
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60)),
):
    """批量更新运行时配置（即时生效，写入审计日志）"""
    if not request.values:
        raise HTTPException(status_code=400, detail="values 不能为空")

    _require_runtime_config_admin(user_id)
    try:
        result = await runtime_config.set_values(request.values, updated_by=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 审计日志（静默失败不影响主流程）
    await write_audit_log(
        db,
        user_id=user_id,
        action="update",
        resource_type="runtime_config",
        detail={"values": request.values},
    )
    await db.commit()

    return success_response(message="配置已更新并即时生效", data=result)


@runtime_config_router.post("/reset")
async def reset_runtime_configs(
        request: RuntimeConfigResetRequest,
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60)),
):
    """重置运行时配置为默认值（keys 为空时重置全部）"""
    keys = request.keys or []

    _require_runtime_config_admin(user_id)
    try:
        result = await runtime_config.reset_values(keys, updated_by=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await write_audit_log(
        db,
        user_id=user_id,
        action="reset",
        resource_type="runtime_config",
        detail={"keys": result["reset"]},
    )
    await db.commit()

    return success_response(message="配置已重置为默认值", data=result)
