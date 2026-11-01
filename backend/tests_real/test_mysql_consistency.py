"""Real MySQL contention and recovery against production repositories."""
import asyncio
import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.sql import Select

from app.models.cleanup_task import DocumentCleanupTask
from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
from app.models.knowledge_insight import (
    KnowledgeArtifact, KnowledgeSourceVersion, SourceType,
)
from app.models.note import Note
from app.repositories.cleanup_task_repository import CleanupTaskRepository
from app.repositories.document_index_repository import DocumentIndexRepository
from app.services.document_index_service import (
    _ExecutionRepository, confirm_publish_commit, reconcile_index_execution,
)
from app.services.insight_generation_service import (
    MindmapConflict, mindmap_revision, save_mindmap_edits,
)
from app.services.note_graph_service import get_note_graph
from app.schemas.insight_models import MindmapNodeEdit


@pytest.mark.asyncio
async def test_m01_concurrent_claim_and_stale_takeover(mysql_sessions):
    owner = str(uuid.uuid4())
    async with mysql_sessions() as session:
        repo = DocumentIndexRepository(session)
        doc = await repo.create(
            user_id=owner, filename="case.txt", original_filename="case.txt",
            file_path="/acceptance/case.txt", md5="a" * 32,
            status=DocumentIndexStatus.PENDING_INDEX,
        )
        doc_id = doc.id
        await session.commit()

    ready = asyncio.Queue()
    release = asyncio.Event()

    async def claim():
        async with mysql_sessions() as session:
            connection_id = (await session.execute(text("SELECT CONNECTION_ID()"))).scalar_one()
            await session.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            await ready.put(connection_id)
            await release.wait()
            generation = await DocumentIndexRepository(session).claim_for_indexing(doc_id)
            await session.commit()
            return generation

    tasks = [asyncio.create_task(claim()) for _ in range(2)]
    try:
        ids = [await asyncio.wait_for(ready.get(), 10) for _ in range(2)]
        assert len(set(ids)) == 2, "claim participants must have independent MySQL connections"
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    print(f"M01 claim connection IDs={ids} generations={results}")
    assert sorted(results, key=lambda value: value or 0) == [None, 1]

    # Failed -> pending -> indexing -> failed returns to the same state but
    # with a new generation. A caller holding the old identity must lose.
    async with mysql_sessions() as session:
        repo = DocumentIndexRepository(session)
        assert await repo.mark_index_failed_if_indexing(doc_id, "first", generation=1)
        assert await repo.reset_to_pending_if_failed(doc_id)
        assert await repo.claim_for_indexing(doc_id) == 2
        assert await repo.mark_index_failed_if_indexing(doc_id, "second", generation=2)
        await session.commit()
    async with mysql_sessions() as session:
        repo = DocumentIndexRepository(session)
        assert not await repo.takeover_upload_row(
            doc_id=doc_id, user_id=owner, filename="stale.txt",
            file_path="/acceptance/stale.txt", md5="b" * 32,
            file_size=1, file_type="txt", expected_generation=1,
            expected_file_path="/acceptance/case.txt",
        )
        await session.commit()
    async with mysql_sessions() as session:
        doc = await DocumentIndexRepository(session).get_by_id(doc_id, owner)
        assert doc.status == DocumentIndexStatus.INDEX_FAILED
        assert doc.generation == 2
        assert doc.file_path == "/acceptance/case.txt"
        assert doc.active_generation == 0


@pytest.mark.asyncio
async def test_m02_candidate_recheck_and_stale_tokens(mysql_sessions):
    """Change due time after candidate SELECT, before the repository UPDATE."""
    async with mysql_sessions() as session:
        task = await CleanupTaskRepository(session).create_task(
            document_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()),
            md5="c" * 32, generation=1, reason="document_deleted",
        )
        task_id = task.id
        await session.commit()

    selected, release = asyncio.Event(), asyncio.Event()

    class PausedSession:
        def __init__(self, inner):
            self.inner = inner
            self.paused = False

        async def execute(self, statement, *args, **kwargs):
            result = await self.inner.execute(statement, *args, **kwargs)
            if isinstance(statement, Select) and not self.paused:
                self.paused = True
                selected.set()
                await release.wait()
            return result

    async def claim():
        async with mysql_sessions() as session:
            connection_id = (await session.execute(text("SELECT CONNECTION_ID()"))).scalar_one()
            await session.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            claimed = await CleanupTaskRepository(PausedSession(session)).claim_due_tasks()
            await session.commit()
            return connection_id, claimed

    pending = asyncio.create_task(claim())
    try:
        await asyncio.wait_for(selected.wait(), 10)
        async with mysql_sessions() as session:
            modifier_id = (await session.execute(text("SELECT CONNECTION_ID()"))).scalar_one()
            later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            await session.execute(update(DocumentCleanupTask).where(
                DocumentCleanupTask.id == task_id
            ).values(next_retry_at=later))
            await session.commit()
        release.set()
        claimant_id, claimed = await asyncio.wait_for(pending, 15)
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert claimant_id != modifier_id
    assert all(item.id != task_id for item in claimed)
    print(f"M02 claimant={claimant_id} modifier={modifier_id} candidate deferred")

    async with mysql_sessions() as session:
        repo = CleanupTaskRepository(session)
        assert not await repo.mark_done(task_id, "stale-token")
        assert not await repo.mark_blocked(task_id, "stale-token", "stale")
        assert await repo.mark_failed(task_id, "stale-token", "stale") == "lost"
        await session.execute(update(DocumentCleanupTask).where(
            DocumentCleanupTask.id == task_id
        ).values(next_retry_at=datetime.now(timezone.utc).replace(tzinfo=None)
                 - timedelta(hours=1),
                 status="pending", claim_token=None, lease_expires_at=None))
        await session.commit()
    async with mysql_sessions() as session:
        claimed = await CleanupTaskRepository(session).claim_due_tasks()
        assert any(item.id == task_id for item in claimed)
        await session.commit()


async def _new_document(mysql_sessions, owner, md5, suffix):
    async with mysql_sessions() as session:
        doc = await DocumentIndexRepository(session).create(
            user_id=owner, filename=f"case-{suffix}.txt",
            original_filename=f"case-{suffix}.txt",
            file_path=f"/tmp/nb-acceptance-{suffix}.txt", md5=md5,
            status=DocumentIndexStatus.PENDING_INDEX,
        )
        await session.commit()
        return doc.id


async def _publish_generation(session, *, doc_id, owner, md5, generation, token,
                              with_cleanup=True):
    repo = DocumentIndexRepository(session)
    executions = _ExecutionRepository(session)
    await executions.open_execution(
        document_id=doc_id, user_id=owner, generation=generation, token=token)
    if with_cleanup:
        await CleanupTaskRepository(session).create_task(
            document_id=doc_id, user_id=owner, md5=md5,
            generation=generation, reason="superseded_generation")
    assert await repo.publish_indexed(doc_id, 2, generation=generation)
    assert await executions.close_execution(
        document_id=doc_id, generation=generation, token=token,
        status="closed", reason="published")
    await session.commit()


@pytest.mark.asyncio
async def test_m03_commit_confirmation_generation_race_and_connection_loss(mysql_sessions):
    owner = str(uuid.uuid4())
    token = str(uuid.uuid4())
    md5 = "e" * 32
    doc_id = await _new_document(mysql_sessions, owner, md5, "m03")

    async with mysql_sessions() as session:
        generation = await DocumentIndexRepository(session).claim_upload_generation(doc_id)
        assert generation == 1
        await _publish_generation(session, doc_id=doc_id, owner=owner, md5=md5,
                                  generation=1, token=token)
    async with mysql_sessions() as session:
        assert await confirm_publish_commit(
            session, document_id=doc_id, user_id=owner, md5=md5,
            generation=1, execution_token=token)

    # Commit-then-connection-loss: confirmation must come from a new connection
    # after the writer's connection is actually killed.
    owner2 = str(uuid.uuid4())
    doc2 = await _new_document(mysql_sessions, owner2, "f" * 32, "m03-kill")
    token2 = str(uuid.uuid4())
    connection_id = None
    killed = False
    session = mysql_sessions()
    await session.__aenter__()
    try:
        generation = await DocumentIndexRepository(session).claim_upload_generation(doc2)
        await _publish_generation(session, doc_id=doc2, owner=owner2, md5="f" * 32,
                                  generation=generation, token=token2)
        # The commit above released the writer connection. Check out a new one
        # and hold its transaction so KILL cannot target the killer itself.
        connection_id = (await session.execute(
            text("SELECT CONNECTION_ID()"))).scalar_one()
        async with mysql_sessions() as killer:
            await killer.execute(text(f"KILL {connection_id}"))
            await killer.commit()
        killed = True
    finally:
        if killed:
            with contextlib.suppress(Exception):
                await session.__aexit__(None, None, None)
        else:
            await session.__aexit__(None, None, None)
    assert killed, "writer connection was not actually killed"
    async with mysql_sessions() as verify:
        assert await confirm_publish_commit(
            verify, document_id=doc2, user_id=owner2, md5="f" * 32,
            generation=generation, execution_token=token2)
    print(f"M03 killed writer connection {connection_id}; publish facts survived")

    # An uncommitted publication is not a fact. After rollback, a later
    # generation publishes and the old token can never confirm.
    owner3 = str(uuid.uuid4())
    doc3 = await _new_document(mysql_sessions, owner3, "1" * 32, "m03-race")
    async with mysql_sessions() as session:
        generation = await DocumentIndexRepository(session).claim_upload_generation(doc3)
        await _ExecutionRepository(session).open_execution(
            document_id=doc3, user_id=owner3, generation=generation,
            token="old-token")
        await session.rollback()
    async with mysql_sessions() as session:
        generation = await DocumentIndexRepository(session).claim_upload_generation(doc3)
        # The rolled-back claim did not persist, so the next claim is gen 1.
        assert generation == 1
        await _publish_generation(session, doc_id=doc3, owner=owner3, md5="1" * 32,
                                  generation=1, token="new-token")
    async with mysql_sessions() as session:
        assert not await confirm_publish_commit(
            session, document_id=doc3, user_id=owner3, md5="1" * 32,
            generation=1, execution_token="old-token")
        assert await confirm_publish_commit(
            session, document_id=doc3, user_id=owner3, md5="1" * 32,
            generation=1, execution_token="new-token")


@pytest.mark.asyncio
async def test_m04_unknown_reconcile_replay_and_generation_conflict(mysql_sessions):
    from app.services.document_index_service import _ExecutionRepository as ExecutionRepo

    owner = str(uuid.uuid4())
    md5 = "2" * 32
    doc_id = await _new_document(mysql_sessions, owner, md5, "m04")
    file_path = Path(f"/tmp/nb-acceptance-m04-{uuid.uuid4().hex}.txt")
    file_path.write_text("acceptance source", encoding="utf-8")
    async with mysql_sessions() as session:
        await session.execute(update(DocumentIndex).where(
            DocumentIndex.id == doc_id).values(file_path=str(file_path)))
        await session.commit()

    token = str(uuid.uuid4())
    async with mysql_sessions() as session:
        generation = await DocumentIndexRepository(session).claim_upload_generation(doc_id)
        await ExecutionRepo(session).open_execution(
            document_id=doc_id, user_id=owner, generation=generation, token=token)
        await session.execute(update(DocumentIndexExecution).where(
            DocumentIndexExecution.document_id == doc_id,
            DocumentIndexExecution.generation == generation,
        ).values(lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                 - timedelta(seconds=1)))
        await session.commit()
    async with mysql_sessions() as session:
        assert await ExecutionRepo(session).mark_expired_writers_unknown(10) == 1
        await session.commit()
    waiting = await reconcile_index_execution(
        document_id=doc_id, user_id=owner, generation=generation,
        execution_token=token)
    assert waiting["outcome"] == "waiting"

    async with mysql_sessions() as session:
        execution_id = (await session.execute(select(DocumentIndexExecution.execution_id).where(
            DocumentIndexExecution.document_id == doc_id,
            DocumentIndexExecution.generation == generation,
            DocumentIndexExecution.token == token,
        ))).scalar_one()
        assert await ExecutionRepo(session).resolve_unknown(execution_id, "writer stopped")
        await session.commit()
    recovered = await reconcile_index_execution(
        document_id=doc_id, user_id=owner, generation=generation,
        execution_token=token)
    assert recovered["outcome"] == "recovered"
    async with mysql_sessions() as session:
        doc = await session.get(DocumentIndex, doc_id)
        assert doc.status == DocumentIndexStatus.INDEX_FAILED
        assert doc.generation == generation
        execution = await session.get(DocumentIndexExecution, execution_id)
        assert execution.status == ExecutionStatus.CLOSED
        assert execution.recovery_outcome == "recovered"
        assert execution.recovered_at is not None
    replay = await reconcile_index_execution(
        document_id=doc_id, user_id=owner, generation=generation,
        execution_token=token)
    assert replay["outcome"] == "recovered"
    file_path.unlink(missing_ok=True)

    # A concurrent generation switch must produce conflict, not overwrite.
    owner2 = str(uuid.uuid4())
    doc2 = await _new_document(mysql_sessions, owner2, "3" * 32, "m04-conflict")
    token2 = str(uuid.uuid4())
    async with mysql_sessions() as session:
        generation2 = await DocumentIndexRepository(session).claim_upload_generation(doc2)
        await ExecutionRepo(session).open_execution(
            document_id=doc2, user_id=owner2, generation=generation2, token=token2)
        await ExecutionRepo(session).close_execution(
            document_id=doc2, generation=generation2, token=token2,
            status="closed", reason="writer stopped")
        await session.commit()
    async with mysql_sessions() as session:
        await session.execute(update(DocumentIndex).where(DocumentIndex.id == doc2).values(
            status=DocumentIndexStatus.INDEXED, generation=2, active_generation=2))
        await session.commit()
    conflict = await reconcile_index_execution(
        document_id=doc2, user_id=owner2, generation=generation2,
        execution_token=token2)
    assert conflict["outcome"] == "conflict"

    # Deletion before recovery is gone, not a resurrected index_failed row.
    owner3 = str(uuid.uuid4())
    doc3 = await _new_document(mysql_sessions, owner3, "4" * 32, "m04-gone")
    token3 = str(uuid.uuid4())
    async with mysql_sessions() as session:
        generation3 = await DocumentIndexRepository(session).claim_upload_generation(doc3)
        await ExecutionRepo(session).open_execution(
            document_id=doc3, user_id=owner3, generation=generation3, token=token3)
        await ExecutionRepo(session).close_execution(
            document_id=doc3, generation=generation3, token=token3,
            status="closed", reason="writer stopped")
        await session.commit()
    async with mysql_sessions() as session:
        await session.execute(text("DELETE FROM document_index WHERE id=:id"), {"id": doc3})
        await session.commit()
    gone = await reconcile_index_execution(
        document_id=doc3, user_id=owner3, generation=generation3,
        execution_token=token3)
    assert gone["outcome"] == "gone"


@pytest.mark.asyncio
async def test_m05_mindmap_revision_and_json_tag_filter(mysql_sessions):
    owner = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    payload = {
        "root": {"id": "root", "label": "root", "children": [
            {"id": "child-1", "label": "child", "children": []},
        ]},
        "node_count": 2,
        "max_depth": 1,
    }
    revision = mindmap_revision([], payload=payload, source_version_id=version_id)
    async with mysql_sessions() as session:
        session.add(KnowledgeSourceVersion(
            id=version_id, owner_user_id=owner, source_type=SourceType.DOCUMENT,
            source_id=doc_id, generation=1, status="active", chunk_count=1))
        session.add(KnowledgeArtifact(
            id=str(uuid.uuid4()), owner_user_id=owner,
            source_version_id=version_id, source_type=SourceType.DOCUMENT,
            source_id=doc_id, generation=1, artifact_type="mindmap",
            schema_version="1.0", payload_json=payload, status="active"))
        note_prefix = f"m05-{uuid.uuid4().hex[:10]}"
        for index, (title, tags) in enumerate([
            ("a", ["a"]), ("b1", ["b"]), ("b2", ["b"]), ("c", ["c"]),
        ]):
            session.add(Note(id=f"{note_prefix}-{index}", user_id=owner,
                             title=title, content=title, tags=tags))
        await session.commit()

    async with mysql_sessions() as first, mysql_sessions() as second:
        await first.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
        await second.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
        await save_mindmap_edits(
            first, owner_user_id=owner, source_type=SourceType.DOCUMENT,
            source_id=doc_id, expected_revision=revision,
            edits=[MindmapNodeEdit(node_id="child-1", label="first")])
        await first.commit()
        with pytest.raises(MindmapConflict):
            await save_mindmap_edits(
                second, owner_user_id=owner, source_type=SourceType.DOCUMENT,
                source_id=doc_id, expected_revision=revision,
                edits=[MindmapNodeEdit(node_id="child-1", label="second")])
        await second.rollback()

    async with mysql_sessions() as session:
        graph = await get_note_graph(
            session, owner_user_id=owner, tag="b", limit=1)
        assert len(graph["nodes"]) == 1
        assert graph["nodes"][0]["title"] in {"b1", "b2"}
        assert graph["counts"]["truncated"] is True
        filtered = await get_note_graph(
            session, owner_user_id=owner, category=None, tag=None, limit=10)
        assert len(filtered["nodes"]) == 4
        other = await get_note_graph(
            session, owner_user_id=str(uuid.uuid4()), tag="b", limit=10)
        assert other["nodes"] == []
