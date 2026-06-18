"""
审计日志工具 —— 记录操作日志，只增不删不更新。

审计日志采用追加写入模式（Append-Only），不允许修改或删除已有记录。
每次业务操作（创建、更新、删除、登录等）都会调用 write_audit_log 记录日志。
写入失败时仅记录警告日志，不会中断主业务流程。
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.core.logger_handler import logger


async def write_audit_log(
    db: AsyncSession,
    user_id: str,
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    org_id: Optional[str] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
):
    """
    写入审计日志。静默失败，不影响主业务流程。

    创建一条审计日志记录并 flush 到数据库（不提交事务），由调用方决定何时 commit。
    写入失败时仅记录 warning 级别日志，不会抛出异常。

    参数:
        db (AsyncSession): 异步数据库会话，由调用方提供，日志随业务事务一起提交。
        user_id (str): 操作者用户 ID。
        action (str): 操作类型，如 create、update、delete、login、upload、invite、remove、add 等。
        resource_type (str, optional): 资源类型，如 note、knowledge、space、member、org 等。
        resource_id (str, optional): 被操作的资源 ID。
        org_id (str, optional): 关联的组织 ID，用于按组织筛选审计日志。
        detail (dict, optional): 操作详情字典，如包含 old_role/new_role、invited_user 等。
        ip_address (str, optional): 客户端 IP 地址。

    关键逻辑:
        - 使用 db.add() + db.flush() 将日志写入数据库但不提交事务。
        - 调用方在业务操作完成后统一 commit，确保日志与业务数据的原子性。
        - 所有异常被捕获并记录为 warning，确保审计日志故障不影响主业务。
    """
    try:
        log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            org_id=org_id,
            detail=detail,
            ip_address=ip_address,
        )
        db.add(log)
        await db.flush()
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")
