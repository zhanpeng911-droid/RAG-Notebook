"""Celery entry points for the durable note-vector outbox.

``apply_note_vector_task`` is the immediate fast path after a note save/delete.
``dispatch_note_vector_tasks_task`` is the Beat compensation path: it claims due
rows and executes them inline, so it does not depend on a healthy broker.
"""
import asyncio
import json

from app.tasks.celery_app import celery_app
from app.core.logger_handler import logger

LEASE_SECONDS = 15 * 60
# One Chroma/SQLite upsert per note made the notes worker fsync-bound (~9/s),
# below the 10 saves/s steady profile. Coalescing due rows amortizes the
# native write and DB sessions; duplicate messages for already-done rows are
# harmless no-ops.
BATCH_SIZE = 200


@celery_app.task(ignore_result=True)
def apply_note_vector_task(note_id: str, user_id: str):
    from app.tasks.index_task import _run_async

    _run_async(_apply_for_note(note_id, user_id))


@celery_app.task(ignore_result=True)
def dispatch_note_vector_tasks_task(limit: int = 50):
    from app.tasks.index_task import _run_async

    _run_async(_dispatch_due(limit))


async def _apply_for_note(note_id: str, user_id: str) -> None:
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.note_vector_task_repository import NoteVectorTaskRepository

    async with AsyncSessionLocal() as session:
        repo = NoteVectorTaskRepository(session)
        claimed = await repo.claim_for_note(
            owner_user_id=user_id, note_id=note_id, lease_seconds=LEASE_SECONDS)
        batch = [claimed] if claimed is not None else []
        if claimed is not None:
            batch.extend(await repo.claim_due(
                BATCH_SIZE - 1, lease_seconds=LEASE_SECONDS))
        await session.commit()
    if batch:
        await _execute_claimed_batch(batch)


async def _dispatch_due(limit: int) -> int:
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.note_vector_task_repository import NoteVectorTaskRepository

    async with AsyncSessionLocal() as session:
        claimed = await NoteVectorTaskRepository(session).claim_due(
            limit, lease_seconds=LEASE_SECONDS)
        await session.commit()
    for start in range(0, len(claimed), BATCH_SIZE):
        await _execute_claimed_batch(claimed[start:start + BATCH_SIZE])
    return len(claimed)


async def _execute_claimed(claimed: dict) -> None:
    await _execute_claimed_batch([claimed])


async def _execute_claimed_batch(claimed: list[dict]) -> None:
    """Index/delete a batch of claimed outbox rows, then write back outcomes."""
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.note_vector_task_repository import NoteVectorTaskRepository
    from app.services.note_service import note_service

    if not claimed:
        return
    errors: dict[str, str] = {}
    upserts = [item for item in claimed if item["action"] != "delete"]
    deletes = [item for item in claimed if item["action"] == "delete"]

    notes = {}
    if upserts:
        async with AsyncSessionLocal() as session:
            batch_notes = await note_service.note_repo.get_by_identities(
                session, [(item["owner_user_id"], item["note_id"]) for item in upserts])
            by_identity = {(note.user_id, note.id): note for note in batch_notes}
            notes = {item["id"]: by_identity[(item["owner_user_id"], item["note_id"])]
                     for item in upserts
                     if (item["owner_user_id"], item["note_id"]) in by_identity}
    to_index = [item for item in upserts if item["id"] in notes]
    missing = [item for item in upserts if item["id"] not in notes]

    if to_index:
        try:
            await asyncio.to_thread(
                note_service.note_index.upsert_notes,
                [(notes[item["id"]].id, notes[item["id"]].user_id,
                  notes[item["id"]].title, notes[item["id"]].content)
                 for item in to_index])
        except Exception as exc:
            from app.rag.vector_io import exception_details
            error = f"{type(exc).__name__}: note batch upsert failed"
            for item in to_index:
                errors[item["id"]] = error
            details = getattr(exc, "details", exception_details(exc))
            logger.error("【笔记向量】批量同步失败 count=%s details=%s",
                         len(to_index), json.dumps(details, ensure_ascii=False))

    for item in missing:
        deletes.append(item)
    if deletes:
        note_ids = [item["note_id"] for item in deletes]
        try:
            await asyncio.to_thread(note_service.note_index.delete_notes, note_ids)
        except Exception as exc:
            from app.rag.vector_io import exception_details
            error = f"{type(exc).__name__}: note batch delete failed"
            for item in deletes:
                errors[item["id"]] = error
            logger.error("【笔记向量】批量删除失败 count=%s details=%s",
                         len(deletes), json.dumps(getattr(exc, "details", exception_details(exc)),
                                                 ensure_ascii=False))

    async with AsyncSessionLocal() as session:
        repo = NoteVectorTaskRepository(session)
        succeeded = [item for item in claimed if item["id"] not in errors]
        await repo.mark_many_done(succeeded)
        for item in claimed:
            error = errors.get(item["id"])
            if error:
                await repo.mark_failed(
                    task_id=item["id"], claim_token=item["claim_token"],
                    version=item["version"], error=error)
        await session.commit()
