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


# ==================== N1：索引执行恢复运维入口 ====================

class IndexExecutionReconcileRequest(BaseModel):
    """恢复请求：精确执行身份 + 核验凭据 + dry-run 支持。"""
    document_id: str
    user_id: str
    generation: int
    execution_token: str
    reason: str = ""               # 非空：人工核验原因
    evidence: str = ""             # 证据位置/说明
    dry_run: bool = True           # 默认 dry-run，apply 需显式 False


@runtime_config_router.post("/reconcile-index-execution")
async def reconcile_index_execution_endpoint(
        request: IndexExecutionReconcileRequest,
        operator_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60)),
):
    """N1 受控恢复：核验停止的执行可在文档仍悬挂时恢复到可重试协议。

    权限：仅 RUNTIME_CONFIG_ADMIN_USER_IDS 内运维；需提供非空原因与证据位置。
    默认 dry-run 只评估不写；apply（dry_run=False）才执行恢复。
    每次调用写入审计日志（operator、目标、dry-run、原因、证据、结果）。
    """
    _require_runtime_config_admin(operator_id)
    if not request.reason.strip():
        raise HTTPException(status_code=400, detail="必须提供非空核验原因")
    if not request.evidence.strip():
        raise HTTPException(status_code=400, detail="必须提供证据位置（维护窗口/排空记录）")

    from app.services.document_index_service import reconcile_index_execution

    if request.dry_run:
        # dry-run：只评估恢复判定（不写文档状态）
        from app.db.db_config import AsyncSessionLocal
        from sqlalchemy import select as _select
        from app.models.document_index import DocumentIndex
        from app.models.index_execution import DocumentIndexExecution
        async with AsyncSessionLocal() as s:
            exec_s = (await s.execute(
                _select(DocumentIndexExecution.status).where(
                    DocumentIndexExecution.document_id == request.document_id,
                    DocumentIndexExecution.user_id == request.user_id,
                    DocumentIndexExecution.generation == request.generation,
                    DocumentIndexExecution.token == request.execution_token,
                )
            )).scalar_one_or_none()
            doc_s = (await s.execute(
                _select(DocumentIndex.status, DocumentIndex.generation).where(
                    DocumentIndex.id == request.document_id,
                    DocumentIndex.user_id == request.user_id,
                )
            )).one_or_none()
        dry_eval = {
            "execution_status": exec_s,
            "document_status": doc_s[0] if doc_s else None,
            "document_generation": doc_s[1] if doc_s else None,
        }
        await write_audit_log(
            db, user_id=operator_id, action="reconcile_dry_run",
            resource_type="document_index_execution",
            detail={"request": request.model_dump(), "eval": dry_eval},
        )
        await db.commit()
        return success_response(
            message="dry-run 完成（未写文档状态）",
            data={"dry_run": True, "evaluation": dry_eval})

    outcome = await reconcile_index_execution(
        document_id=request.document_id,
        user_id=request.user_id,
        generation=request.generation,
        execution_token=request.execution_token,
    )
    await write_audit_log(
        db, user_id=operator_id, action="reconcile_apply",
        resource_type="document_index_execution",
        detail={"request": request.model_dump(), "outcome": outcome},
    )
    await db.commit()

    status_code = 200 if outcome["outcome"] in ("recovered", "published", "needs_reupload") else 409
    return success_response(
        message=outcome["message"],
        data={"outcome": outcome["outcome"], "dry_run": False,
              "message": outcome["message"]},
    ) if status_code == 200 else HTTPException(
        status_code=status_code,
        detail=f"{outcome['outcome']}: {outcome['message']}")
