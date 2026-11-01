"""T3: real Redis/Celery worker delivery, compensation and idempotency."""
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
from app.models.knowledge_insight import (
    JobStatus, KnowledgeArtifact, KnowledgeGenerationJob,
)
from app.models.note_vector_task import NoteVectorTask, NoteVectorTaskStatus
from app.repositories.document_index_repository import DocumentIndexRepository
from app.services.insight_generation_service import queue_summary_generation
from app.services.note_service import note_service
from app.schemas.models import NoteCreate

RUN = os.environ["NB_ACCEPTANCE_RUN_ID"]
ROOT = Path("/app/data/acceptance-recovery") / RUN


def _vector_ids(owner, document_id=None, note_id=None):
    from app.rag.vector_store import VectorStoreService
    store = VectorStoreService().vectors_store
    clauses = [{"user_id": owner}]
    if document_id is not None:
        clauses.append({"document_id": document_id})
    if note_id is not None:
        clauses.append({"note_id": note_id})
    result = store.get(where={"$and": clauses})
    return result["ids"]


def _note_vector_ids(owner, note_id):
    """Read notes_collection in a fresh process (worker writes are persistent)."""
    import json
    import subprocess
    import sys
    script = """
import chromadb, json, os, sys
client = chromadb.PersistentClient(path=os.environ['NB_ACCEPTANCE_CHROMA_DIR'])
collection = client.get_collection('notes_collection')
result = collection.get(where={'$and': [
    {'user_id': sys.argv[1]}, {'note_id': sys.argv[2]}, {'doc_type': 'note'}]})
print(json.dumps(result['ids']))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, owner, note_id],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


async def _poll(description, predicate, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(1)
    raise AssertionError(f"timed out waiting for {description}")


@pytest.mark.asyncio
async def test_p01_duplicate_index_messages_publish_once(mysql_sessions):
    owner = str(uuid.uuid4())
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / f"p01-{uuid.uuid4().hex}.txt"
    path.write_text("P01 duplicate delivery body.\n" * 50, encoding="utf-8")
    async with mysql_sessions() as session:
        doc = await DocumentIndexRepository(session).create(
            user_id=owner, filename=path.name, original_filename=path.name,
            file_path=str(path), md5="a" * 32, file_size=path.stat().st_size,
            file_type="txt", status=DocumentIndexStatus.PENDING_INDEX)
        doc_id = doc.id
        await session.commit()

    from app.tasks.index_task import index_document_task
    index_document_task.delay(doc_id, owner)
    index_document_task.delay(doc_id, owner)

    async def finished():
        async with mysql_sessions() as session:
            row = await session.get(DocumentIndex, doc_id)
            return row if row.status in (DocumentIndexStatus.INDEXED,
                                         DocumentIndexStatus.INDEX_FAILED) else None
    row = await _poll("index terminal state", finished)
    assert row.status == DocumentIndexStatus.INDEXED, row.error_message
    assert row.active_generation == 1
    async with mysql_sessions() as session:
        executions = list((await session.execute(select(DocumentIndexExecution).where(
            DocumentIndexExecution.document_id == doc_id))).scalars().all())
    assert len(executions) == 1
    assert executions[0].generation == 1
    assert executions[0].status == ExecutionStatus.CLOSED
    ids = _vector_ids(owner, document_id=doc_id)
    assert ids
    print(json.dumps({"case": "P01", "generation": row.active_generation,
                      "executions": len(executions), "vectors": len(ids)}))


@pytest.mark.asyncio
async def test_p04_queued_analysis_is_dispatched_by_compensator(mysql_sessions):
    from app.tasks.insight_task import _dispatch_queued_jobs

    owner = str(uuid.uuid4())
    async with mysql_sessions() as session:
        note = await note_service.create_note(
            session, owner,
            NoteCreate(title="P04 queued analysis", content=(
                "第一段：补偿投递需要真实 broker。\n\n"
                "第二段：worker 领取后只执行一次。\n\n"
                "第三段：结果必须可读。")))
    async with mysql_sessions() as session:
        job, already, _ = await queue_summary_generation(
            session, owner_user_id=owner, source_type="note",
            source_id=note.id, force=True)
        job_id = job.id
        assert already is False
        await session.execute(update(KnowledgeGenerationJob).where(
            KnowledgeGenerationJob.id == job_id).values(
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=2)))
        await session.commit()
    # Simulate the commit-to-dispatch gap: no direct dispatch happened. Beat
    # may have raced us; in that case its dispatch is the compensation path.
    async with mysql_sessions() as session:
        queued = (await session.get(KnowledgeGenerationJob, job_id)).status == JobStatus.QUEUED
    if queued:
        assert await _dispatch_queued_jobs(10) >= 1

    async def done():
        async with mysql_sessions() as session:
            row = await session.get(KnowledgeGenerationJob, job_id)
            return row if row.status in (JobStatus.SUCCEEDED, JobStatus.FAILED,
                                         JobStatus.STALE) else None
    row = await _poll("analysis job terminal state", done)
    assert row.status == JobStatus.SUCCEEDED, row.error
    assert row.attempts == 1
    assert row.last_dispatch_at is not None
    async with mysql_sessions() as session:
        artifact = (await session.execute(select(KnowledgeArtifact).where(
            KnowledgeArtifact.source_version_id == row.source_version_id,
            KnowledgeArtifact.artifact_type == "summary",
        ))).scalar_one_or_none()
    assert artifact is not None
    print(json.dumps({"case": "P04", "job": job_id, "attempts": row.attempts,
                      "trust_status": artifact.payload_json.get("trust_status")}))


@pytest.mark.asyncio
async def test_p03_note_outbox_duplicate_worker_and_delete_converge(mysql_sessions):
    from app.tasks.note_vector_task import apply_note_vector_task

    owner = str(uuid.uuid4())
    async with mysql_sessions() as session:
        note = await note_service.create_note(
            session, owner,
            NoteCreate(title="P03 outbox", content="outbox vector body"))

    async def task_done(action):
        async with mysql_sessions() as session:
            row = (await session.execute(select(NoteVectorTask).where(
                NoteVectorTask.note_id == note.id))).scalar_one()
            return row if row.action == action and row.status == NoteVectorTaskStatus.DONE else None
    apply_note_vector_task.delay(note.id, owner)
    apply_note_vector_task.delay(note.id, owner)
    assert await _poll("note vector upsert", lambda: task_done("upsert"))
    assert _note_vector_ids(owner, note.id)

    async with mysql_sessions() as session:
        assert await note_service.delete_note(session, note.id, owner)
    apply_note_vector_task.delay(note.id, owner)
    assert await _poll("note vector delete", lambda: task_done("delete"))
    assert _note_vector_ids(owner, note.id) == []
    print(json.dumps({"case": "P03", "note": note.id, "duplicate_safe": True}))
