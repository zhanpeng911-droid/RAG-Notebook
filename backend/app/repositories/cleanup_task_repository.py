"""
文档清理任务仓库（C4）——持久清理队列的领取与状态回写。

公平领取：按 next_retry_at, created_at, id 有界选取（两阶段物化 ID 列表，
兼容 MySQL 8.0），条件写 claim_token 与租约；租约过期的 claimed 任务可被
重新领取（worker 崩溃恢复）。清理动作在事务外执行。
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select, update

from app.models.cleanup_task import DocumentCleanupTask, CleanupTaskStatus

CLAIM_LEASE_MINUTES = 10
MAX_ATTEMPTS = 5


class WaitingWriterDeferred(Exception):
    """存在未关闭写入执行——挂起重试（不计失败、不退避），关闭后再核验。"""


class UnresolvedCleanupError(Exception):
    """清理目标无法精确归属（遗留向量/映射缺失）——进入 blocked，不重试。"""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CleanupTaskRepository:
    def __init__(self, session):
        self.session = session

    async def create_task(self, *, document_id: str, user_id: str, md5: str,
                          generation: int, reason: str,
                          legacy_chunk_count: int = 0) -> Optional[DocumentCleanupTask]:
        """登记清理任务；目标唯一约束冲突（重复发布/重复删除）时返回 None。"""
        existing = (await self.session.execute(
            select(DocumentCleanupTask).where(
                DocumentCleanupTask.document_id == document_id,
                DocumentCleanupTask.generation == generation,
                DocumentCleanupTask.reason == reason,
            )
        )).scalar_one_or_none()
        if existing:
            return None
        task = DocumentCleanupTask(
            id=str(uuid.uuid4()),
            document_id=document_id,
            user_id=user_id,
            md5=md5,
            generation=generation,
            legacy_chunk_count=legacy_chunk_count,
            reason=reason,
            status=CleanupTaskStatus.PENDING,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def claim_due_tasks(self, limit: int = 20) -> List[DocumentCleanupTask]:
        """公平领取到期任务（两阶段物化 ID，兼容 MySQL），条件写令牌与租约。"""
        now = _now()
        bounded = max(1, min(int(limit), 200))
        due_ids = list((await self.session.execute(
            select(DocumentCleanupTask.id)
            .where(DocumentCleanupTask.status.in_([
                CleanupTaskStatus.PENDING, CleanupTaskStatus.FAILED,
                CleanupTaskStatus.CLAIMED,
            ]))
            .where((DocumentCleanupTask.next_retry_at.is_(None))
                   | (DocumentCleanupTask.next_retry_at <= now))
            .where((DocumentCleanupTask.status != CleanupTaskStatus.CLAIMED)
                   | (DocumentCleanupTask.lease_expires_at.is_(None))
                   | (DocumentCleanupTask.lease_expires_at <= now))
            .order_by(DocumentCleanupTask.next_retry_at.asc(),
                      DocumentCleanupTask.created_at.asc(),
                      DocumentCleanupTask.id.asc())
            .limit(bounded)
        )).scalars().all())
        if not due_ids:
            return []

        token = str(uuid.uuid4())
        lease = now + timedelta(minutes=CLAIM_LEASE_MINUTES)
        await self.session.execute(
            update(DocumentCleanupTask)
            .where(DocumentCleanupTask.id.in_(due_ids))
            .where(DocumentCleanupTask.status.in_([
                CleanupTaskStatus.PENDING, CleanupTaskStatus.FAILED,
                CleanupTaskStatus.CLAIMED,
            ]))
            .where((DocumentCleanupTask.next_retry_at.is_(None))
                   | (DocumentCleanupTask.next_retry_at <= now))
            .where((DocumentCleanupTask.status != CleanupTaskStatus.CLAIMED)
                   | (DocumentCleanupTask.lease_expires_at.is_(None))
                   | (DocumentCleanupTask.lease_expires_at <= now))
            .values(status=CleanupTaskStatus.CLAIMED, claim_token=token,
                    lease_expires_at=lease, updated_at=now)
        )
        claimed = list((await self.session.execute(
            select(DocumentCleanupTask)
            .where(DocumentCleanupTask.claim_token == token)
        )).scalars().all())
        return claimed

    async def mark_verified_empty(self, task_id: str, token: str,
                                  verified_at, grace: timedelta) -> bool:
        """核验为空：登记观测并释放租约回 pending（是否 done 由写入者关闭协议决定）。"""
        result = await self.session.execute(
            update(DocumentCleanupTask)
            .where(DocumentCleanupTask.id == task_id)
            .where(DocumentCleanupTask.claim_token == token)
            .where(DocumentCleanupTask.status == CleanupTaskStatus.CLAIMED)
            .values(verified_empty_at=verified_at, status=CleanupTaskStatus.PENDING,
                    next_retry_at=verified_at + grace, claim_token=None,
                    lease_expires_at=None, updated_at=_now())
        )
        return result.rowcount > 0

    async def defer_for_writer(self, task_id: str, token: str) -> bool:
        """waiting_writer 挂起：回 pending、立即到期、不计失败次数。"""
        result = await self.session.execute(
            update(DocumentCleanupTask)
            .where(DocumentCleanupTask.id == task_id)
            .where(DocumentCleanupTask.claim_token == token)
            .where(DocumentCleanupTask.status == CleanupTaskStatus.CLAIMED)
            .values(status=CleanupTaskStatus.PENDING, next_retry_at=_now(),
                    claim_token=None, lease_expires_at=None, updated_at=_now())
        )
        return result.rowcount > 0

    async def mark_blocked(self, task_id: str, token: str, reason: str) -> bool:
        """归属不清等不可自动恢复情形：进入 blocked（人工可见），不消耗重试预算。"""
        result = await self.session.execute(
            update(DocumentCleanupTask)
            .where(DocumentCleanupTask.id == task_id)
            .where(DocumentCleanupTask.claim_token == token)
            .where(DocumentCleanupTask.status == CleanupTaskStatus.CLAIMED)
            .values(status=CleanupTaskStatus.BLOCKED, last_error=reason[:500],
                    updated_at=_now())
        )
        return result.rowcount > 0

    async def mark_done(self, task_id: str, token: str) -> bool:
        result = await self.session.execute(
            update(DocumentCleanupTask)
            .where(DocumentCleanupTask.id == task_id)
            .where(DocumentCleanupTask.claim_token == token)
            .where(DocumentCleanupTask.status == CleanupTaskStatus.CLAIMED)
            .values(status=CleanupTaskStatus.DONE, updated_at=_now())
        )
        return result.rowcount > 0

    async def mark_failed(self, task_id: str, token: str, error: str) -> str:
        """失败回写：指数退避；超过重试上限进入 blocked（人工可见，不删记录）。"""
        result = await self.session.execute(
            select(DocumentCleanupTask).where(
                DocumentCleanupTask.id == task_id,
                DocumentCleanupTask.claim_token == token,
                DocumentCleanupTask.status == CleanupTaskStatus.CLAIMED,
            )
        )
        task = result.scalar_one_or_none()
        if task is None:
            return "lost"
        attempts = (task.attempt_count or 0) + 1
        now = _now()
        if attempts >= MAX_ATTEMPTS:
            await self.session.execute(
                update(DocumentCleanupTask)
                .where(DocumentCleanupTask.id == task_id)
                .where(DocumentCleanupTask.claim_token == token)  # 令牌条件：防覆盖新领取者
                .values(status=CleanupTaskStatus.BLOCKED, attempt_count=attempts,
                        last_error=error[:500], updated_at=now)
            )
            return "blocked"
        backoff = now + timedelta(seconds=60 * (2 ** min(attempts, 5)))
        await self.session.execute(
            update(DocumentCleanupTask)
            .where(DocumentCleanupTask.id == task_id)
            .where(DocumentCleanupTask.claim_token == token)  # 令牌条件
            .values(status=CleanupTaskStatus.FAILED, attempt_count=attempts,
                    next_retry_at=backoff, last_error=error[:500], updated_at=now)
        )
        return "retry"
