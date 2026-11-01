"""Persistence for the durable note-vector outbox.

All methods flush but never commit; the caller owns the transaction so a note
save and its outbox row are atomic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_, select, update, tuple_

from app.models.note_vector_task import (
    NoteVectorAction, NoteVectorTask, NoteVectorTaskStatus,
)

MAX_ATTEMPTS = 5
BASE_RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 3600


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NoteVectorTaskRepository:
    def __init__(self, session):
        self.session = session

    async def enqueue(self, *, owner_user_id: str, note_id: str,
                      action: str) -> NoteVectorTask:
        """Fold a new save/delete into one row per note, bumping its version."""
        if action not in (NoteVectorAction.UPSERT, NoteVectorAction.DELETE):
            raise ValueError("unknown_note_vector_action")
        # NULL means immediately due. Writing _now() here raced MySQL
        # DATETIME(0) rounding: a row written at xx.7s was stored as xx+1s,
        # so the post-commit fast-path claim saw it as "not due yet".
        # The caller already holds the note row lock (flush before enqueue),
        # which serializes same-note saves. FOR UPDATE here took a gap lock on
        # the unique owner+note index and deadlocked concurrent new-note saves.
        result = await self.session.execute(
            select(NoteVectorTask).where(
                NoteVectorTask.owner_user_id == owner_user_id,
                NoteVectorTask.note_id == note_id,
            )
        )
        task = result.scalar_one_or_none()
        if task is None:
            task = NoteVectorTask(
                id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                note_id=note_id,
                action=action,
                status=NoteVectorTaskStatus.PENDING,
                version=1,
                attempts=0,
                next_retry_at=None,
            )
            self.session.add(task)
        else:
            # A newer save/delete supersedes any in-flight result. The worker
            # claims by version, so the old completion cannot mark it done.
            task.action = action
            task.status = NoteVectorTaskStatus.PENDING
            task.version = int(task.version or 0) + 1
            task.attempts = 0
            task.claim_token = None
            task.claimed_at = None
            task.next_retry_at = None
            task.last_error = None
        await self.session.flush()
        return task

    async def get_for_note(self, *, owner_user_id: str,
                           note_id: str) -> Optional[NoteVectorTask]:
        result = await self.session.execute(
            select(NoteVectorTask).where(
                NoteVectorTask.owner_user_id == owner_user_id,
                NoteVectorTask.note_id == note_id,
            )
        )
        return result.scalar_one_or_none()

    async def claim_due(self, limit: int, *,
                        lease_seconds: int = 900) -> list[dict]:
        """Reset expired leases and atomically claim due rows for Beat dispatch."""
        now = _now()
        await self.session.execute(
            update(NoteVectorTask).where(
                NoteVectorTask.status == NoteVectorTaskStatus.RUNNING,
                NoteVectorTask.claimed_at < now - timedelta(seconds=lease_seconds),
            ).values(
                status=NoteVectorTaskStatus.FAILED,
                claim_token=None,
                claimed_at=None,
                next_retry_at=now,
                last_error="worker_lease_expired",
            )
        )
        result = await self.session.execute(
            select(NoteVectorTask).where(
                NoteVectorTask.status.in_((NoteVectorTaskStatus.PENDING,
                                           NoteVectorTaskStatus.FAILED)),
                or_(NoteVectorTask.next_retry_at.is_(None),
                    NoteVectorTask.next_retry_at <= now),
            ).order_by(
                func.coalesce(NoteVectorTask.next_retry_at,
                              NoteVectorTask.updated_at),
                NoteVectorTask.id,
            ).limit(limit).with_for_update(skip_locked=True)
        )
        return [self._claim(row, now) for row in result.scalars().all()]

    async def claim_for_note(self, *, owner_user_id: str, note_id: str,
                             lease_seconds: int = 900) -> Optional[dict]:
        """Claim one note's due row for an immediate post-commit dispatch."""
        now = _now()
        result = await self.session.execute(
            select(NoteVectorTask).where(
                NoteVectorTask.owner_user_id == owner_user_id,
                NoteVectorTask.note_id == note_id,
                NoteVectorTask.status.in_((NoteVectorTaskStatus.PENDING,
                                           NoteVectorTaskStatus.FAILED)),
                or_(NoteVectorTask.next_retry_at.is_(None),
                    NoteVectorTask.next_retry_at <= now),
            ).with_for_update(skip_locked=True)
        )
        task = result.scalar_one_or_none()
        return self._claim(task, now) if task is not None else None

    @staticmethod
    def _claim(task: NoteVectorTask, now: datetime) -> dict:
        task.status = NoteVectorTaskStatus.RUNNING
        task.claim_token = str(uuid.uuid4())
        task.claimed_at = now
        task.attempts = int(task.attempts or 0) + 1
        return {
            "id": task.id,
            "owner_user_id": task.owner_user_id,
            "note_id": task.note_id,
            "action": task.action,
            "version": int(task.version or 0),
            "claim_token": task.claim_token,
            "attempts": int(task.attempts),
        }

    async def mark_done(self, *, task_id: str, claim_token: str,
                        version: int) -> bool:
        result = await self.session.execute(
            update(NoteVectorTask).where(
                NoteVectorTask.id == task_id,
                NoteVectorTask.claim_token == claim_token,
                NoteVectorTask.version == version,
                NoteVectorTask.status == NoteVectorTaskStatus.RUNNING,
            ).values(
                status=NoteVectorTaskStatus.DONE,
                claim_token=None,
                claimed_at=None,
                next_retry_at=None,
                last_error=None,
            )
        )
        return bool(result.rowcount)

    async def mark_many_done(self, claimed: list[dict]) -> int:
        """One conditional update, retaining each row's token/version fence."""
        if not claimed:
            return 0
        result = await self.session.execute(update(NoteVectorTask).where(
            NoteVectorTask.status == NoteVectorTaskStatus.RUNNING,
            tuple_(NoteVectorTask.id, NoteVectorTask.claim_token, NoteVectorTask.version).in_(
                [(row["id"], row["claim_token"], row["version"]) for row in claimed]),
        ).values(status=NoteVectorTaskStatus.DONE, claim_token=None,
                 claimed_at=None, next_retry_at=None, last_error=None)
            # The worker uses a fresh session. Avoid ORM prefetch/RETURNING
            # synchronization for tuple predicates; retain one UPDATE.
            .execution_options(synchronize_session=False))
        return int(result.rowcount or 0)

    async def mark_failed(self, *, task_id: str, claim_token: str, version: int,
                          error: str) -> str:
        """Return done (lost claim), retry, or blocked."""
        result = await self.session.execute(
            select(NoteVectorTask).where(
                NoteVectorTask.id == task_id,
                NoteVectorTask.claim_token == claim_token,
                NoteVectorTask.version == version,
                NoteVectorTask.status == NoteVectorTaskStatus.RUNNING,
            ).with_for_update()
        )
        task = result.scalar_one_or_none()
        if task is None:
            return "lost"
        attempts = int(task.attempts or 0)
        if attempts >= MAX_ATTEMPTS:
            task.status = NoteVectorTaskStatus.BLOCKED
            task.claim_token = None
            task.claimed_at = None
            task.next_retry_at = None
            task.last_error = error[:2000]
            return "blocked"
        delay = min(MAX_RETRY_SECONDS, BASE_RETRY_SECONDS * (2 ** max(0, attempts - 1)))
        task.status = NoteVectorTaskStatus.FAILED
        task.claim_token = None
        task.claimed_at = None
        task.next_retry_at = _now() + timedelta(seconds=delay)
        task.last_error = error[:2000]
        return "retry"
