"""P03: note save/delete and vector work are joined by a durable outbox."""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.models.chat_history import Base
from app.models.note_vector_task import NoteVectorTask, NoteVectorTaskStatus
from app.repositories.note_vector_task_repository import (
    MAX_ATTEMPTS, NoteVectorTaskRepository,
)
from app.schemas.models import NoteCreate, NoteUpdate
from app.services.note_service import NoteService, note_service

USER = "u-outbox-0000-0000-000000000001"


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=engine, class_=AsyncSession,
                             expire_on_commit=False)
    import app.db.db_config as dbc
    monkeypatch.setattr(dbc, "AsyncSessionLocal", fac)
    # Keep this test focused on the outbox; anchor/link side work has its own
    # regressions and would otherwise add unrelated tables to the assertion.
    async def noop(*args, **kwargs):
        return None
    monkeypatch.setattr(NoteService, "_sync_note_anchors", staticmethod(noop))
    monkeypatch.setattr(NoteService, "_sync_note_links", staticmethod(noop))
    import app.services.note_service as note_service_module
    import app.router.note_router as note_router_module
    monkeypatch.setattr(note_service_module, "get_settings",
                        lambda: SimpleNamespace(NOTE_VECTOR_INDEX_ENABLED=True))
    monkeypatch.setattr(note_router_module, "get_settings",
                        lambda: SimpleNamespace(NOTE_VECTOR_INDEX_ENABLED=True))
    yield fac
    await engine.dispose()


async def _create(fac):
    async with fac() as db:
        return await note_service.create_note(
            db, USER, NoteCreate(title="outbox", content="body"))


async def _task(fac, note_id):
    async with fac() as db:
        return (await db.execute(select(NoteVectorTask).where(
            NoteVectorTask.note_id == note_id))).scalar_one()


@pytest.mark.asyncio
async def test_note_create_commits_pending_outbox(factory):
    note = await _create(factory)
    task = await _task(factory, note.id)
    assert task.status == NoteVectorTaskStatus.PENDING
    assert task.action == "upsert"
    assert task.version == 1


@pytest.mark.asyncio
async def test_fresh_outbox_row_is_immediately_due(factory):
    """NULL means due now; a rounded future DATETIME skipped the fast path."""
    note = await _create(factory)
    task = await _task(factory, note.id)
    assert task.next_retry_at is None
    async with factory() as db:
        claimed = await NoteVectorTaskRepository(db).claim_for_note(
            owner_user_id=USER, note_id=note.id)
        await db.commit()
    assert claimed is not None


@pytest.mark.asyncio
async def test_old_worker_completion_cannot_overwrite_newer_save(factory):
    note = await _create(factory)
    async with factory() as db:
        claimed = await NoteVectorTaskRepository(db).claim_for_note(
            owner_user_id=USER, note_id=note.id)
        await db.commit()
    assert claimed["version"] == 1

    async with factory() as db:
        updated = await note_service.update_note(
            db, note.id, USER, NoteUpdate(content="newer body"))
    assert updated is not None

    async with factory() as db:
        repo = NoteVectorTaskRepository(db)
        assert not await repo.mark_done(
            task_id=claimed["id"], claim_token=claimed["claim_token"],
            version=claimed["version"])
        await db.commit()
    task = await _task(factory, note.id)
    assert task.status == NoteVectorTaskStatus.PENDING
    assert task.version == 2

    async with factory() as db:
        newer = await NoteVectorTaskRepository(db).claim_for_note(
            owner_user_id=USER, note_id=note.id)
        assert newer["version"] == 2
        assert await NoteVectorTaskRepository(db).mark_done(
            task_id=newer["id"], claim_token=newer["claim_token"],
            version=newer["version"])
        await db.commit()
    assert (await _task(factory, note.id)).status == NoteVectorTaskStatus.DONE


@pytest.mark.asyncio
async def test_note_delete_keeps_a_delete_intent(factory):
    note = await _create(factory)
    async with factory() as db:
        assert await note_service.delete_note(db, note.id, USER)
    task = await _task(factory, note.id)
    assert task.action == "delete"
    assert task.status == NoteVectorTaskStatus.PENDING
    assert task.version == 2


@pytest.mark.asyncio
async def test_broker_failure_then_beat_recovers_the_vector(factory, monkeypatch):
    from app.tasks import note_vector_task

    note = await _create(factory)
    sent = []

    def broken_delay(*args, **kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(note_vector_task.apply_note_vector_task, "delay",
                        broken_delay)
    import app.router.note_router as note_router_module
    note_router_module._enqueue_note_vector_sync(note.id, USER)
    assert sent == []

    class FakeIndex:
        def __init__(self):
            self.upserted = []
            self.deleted = []

        def upsert_note(self, *args):
            self.upserted.append(args)

        def upsert_notes(self, items):
            self.upserted.extend(items)

        def delete_note(self, *args):
            self.deleted.append(args)

        def delete_notes(self, note_ids):
            self.deleted.extend((note_id,) for note_id in note_ids)

    fake = FakeIndex()
    monkeypatch.setattr(note_service, "note_index", fake)
    task = await _task(factory, note.id)
    async with factory() as db:
        row = await db.get(NoteVectorTask, task.id)
        row.next_retry_at = None
        await db.commit()

    assert await note_vector_task._dispatch_due(10) == 1
    assert fake.upserted and fake.upserted[0][0] == note.id
    assert (await _task(factory, note.id)).status == NoteVectorTaskStatus.DONE


@pytest.mark.asyncio
async def test_dispatch_coalesces_due_rows_into_one_chroma_write(factory, monkeypatch):
    from app.tasks import note_vector_task

    notes = [await _create(factory) for _ in range(5)]

    class BatchIndex:
        def __init__(self):
            self.batches = []

        def upsert_notes(self, items):
            self.batches.append(list(items))

        def delete_notes(self, note_ids):
            self.batches.append(list(note_ids))

    fake = BatchIndex()
    monkeypatch.setattr(note_service, "note_index", fake)
    note_reads = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select ") and " from notes " in normalized:
            note_reads.append(statement)

    engine = factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert await note_vector_task._dispatch_due(20) == 5
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(note_reads) == 1  # No N+1 note lookup as the batch grows.
    assert len(fake.batches) == 1
    assert {item[0] for item in fake.batches[0]} == {note.id for note in notes}
    for note in notes:
        assert (await _task(factory, note.id)).status == NoteVectorTaskStatus.DONE


@pytest.mark.asyncio
async def test_batch_completion_preserves_newer_save_and_foreign_token(factory):
    notes = [await _create(factory) for _ in range(3)]
    async with factory() as db:
        claimed = await NoteVectorTaskRepository(db).claim_due(3)
        await db.commit()
    by_note = {item["note_id"]: item for item in claimed}
    async with factory() as db:
        await note_service.update_note(db, notes[0].id, USER, NoteUpdate(content="new version"))
    # A stale or forged claim cannot complete either the newer row or a peer row.
    bad_token = {**by_note[notes[1].id], "claim_token": "not-the-worker-token"}
    async with factory() as db:
        repo = NoteVectorTaskRepository(db)
        assert await repo.mark_many_done([
            by_note[notes[0].id], bad_token, by_note[notes[2].id],
        ]) == 1
        assert await repo.mark_many_done([]) == 0
        await db.commit()
    newer = await _task(factory, notes[0].id)
    assert (newer.status, newer.version) == (NoteVectorTaskStatus.PENDING, 2)
    assert (await _task(factory, notes[1].id)).status == NoteVectorTaskStatus.RUNNING
    assert (await _task(factory, notes[2].id)).status == NoteVectorTaskStatus.DONE


@pytest.mark.asyncio
async def test_failed_task_backs_off_and_blocks_after_max_attempts(factory):
    note = await _create(factory)
    async with factory() as db:
        claimed = await NoteVectorTaskRepository(db).claim_for_note(
            owner_user_id=USER, note_id=note.id)
        await db.commit()
    async with factory() as db:
        outcome = await NoteVectorTaskRepository(db).mark_failed(
            task_id=claimed["id"], claim_token=claimed["claim_token"],
            version=claimed["version"], error="temporary")
        await db.commit()
    assert outcome == "retry"
    task = await _task(factory, note.id)
    assert task.status == NoteVectorTaskStatus.FAILED
    assert task.next_retry_at is not None

    async with factory() as db:
        row = await db.get(NoteVectorTask, task.id)
        row.status = NoteVectorTaskStatus.PENDING
        row.next_retry_at = None
        row.attempts = MAX_ATTEMPTS - 1
        await db.commit()
    async with factory() as db:
        claimed = await NoteVectorTaskRepository(db).claim_for_note(
            owner_user_id=USER, note_id=note.id)
        assert claimed["attempts"] == MAX_ATTEMPTS
        outcome = await NoteVectorTaskRepository(db).mark_failed(
            task_id=claimed["id"], claim_token=claimed["claim_token"],
            version=claimed["version"], error="permanent")
        await db.commit()
    assert outcome == "blocked"
    assert (await _task(factory, note.id)).status == NoteVectorTaskStatus.BLOCKED
