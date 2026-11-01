"""V02: real persisted Chroma and production identity-scoped cleanup."""
import asyncio
import json
import os
import subprocess
import sys
import uuid

import pytest
from langchain_core.documents import Document
from sqlalchemy import select, update

from app.models.cleanup_task import DocumentCleanupTask
from app.repositories.cleanup_task_repository import CleanupTaskRepository


def read_in_fresh_process(ids):
    chroma_dir = os.environ.get("NB_ACCEPTANCE_CHROMA_DIR", "/app/data/chromadb")
    script = """
import chromadb, json, sys
client = chromadb.PersistentClient(path=sys.argv[2])
collection = client.get_collection('rag_collection')
result = collection.get(ids=json.loads(sys.argv[1]), include=['metadatas'])
print(json.dumps({'ids': result['ids'], 'metadatas': result['metadatas']}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps(ids), chroma_dir],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.asyncio
async def test_v02_same_md5_identity_and_current_generation(mysql_sessions):
    # mysql_sessions refuses any non-isolated target before constructing the store.
    from app.rag.vector_store import VectorStoreService
    from app.tasks.index_task import _process_cleanup_tasks_async

    owner, doc_a, doc_b = (str(uuid.uuid4()) for _ in range(3))
    md5 = "d" * 32
    ids = [f"{owner}:{doc_a}:g1:0", f"{owner}:{doc_a}:g2:0", f"{owner}:{doc_b}:g1:0"]
    metadata = [
        {"user_id": owner, "document_id": doc_a, "generation": 1, "md5": md5},
        {"user_id": owner, "document_id": doc_a, "generation": 2, "md5": md5},
        {"user_id": owner, "document_id": doc_b, "generation": 1, "md5": md5},
    ]
    store = VectorStoreService().vectors_store
    await asyncio.to_thread(store.add_documents, [
        Document(page_content=f"isolated acceptance vector {index}", metadata=item)
        for index, item in enumerate(metadata)
    ], ids=ids)
    before = await asyncio.to_thread(read_in_fresh_process, ids)
    assert set(before["ids"]) == set(ids)

    async def add_task(reason, generation):
        async with mysql_sessions() as session:
            task = await CleanupTaskRepository(session).create_task(
                document_id=doc_a, user_id=owner, md5=md5,
                generation=generation, reason=reason,
            )
            task_id = task.id
            await session.commit()
            return task_id

    async def task_status(task_id):
        async with mysql_sessions() as session:
            return (await session.execute(select(DocumentCleanupTask.status).where(
                DocumentCleanupTask.id == task_id
            ))).scalar_one()

    superseded_id = await add_task("superseded_generation", 2)
    await _process_cleanup_tasks_async(200)
    assert await task_status(superseded_id) == "done"
    after_old = await asyncio.to_thread(read_in_fresh_process, ids)
    assert set(after_old["ids"]) == {ids[1], ids[2]}

    deleted_id = await add_task("document_deleted", 2)
    await _process_cleanup_tasks_async(200)
    assert await task_status(deleted_id) == "done"
    after_delete = await asyncio.to_thread(read_in_fresh_process, ids)
    assert after_delete["ids"] == [ids[2]]
    assert after_delete["metadatas"][0]["document_id"] == doc_b
    print(f"V02 vector IDs before={before['ids']} after-old={after_old['ids']} "
          f"after-delete={after_delete['ids']}")


@pytest.mark.asyncio
async def test_v01_cancel_late_write_requires_verified_close_then_converges(
        mysql_sessions, tmp_path, monkeypatch):
    """Real persisted Chroma: cancel mid-write, late vector, cleanup gate, resolve."""
    import hashlib
    import threading

    from app.models.document_index import DocumentIndexStatus
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.repositories.document_index_repository import DocumentIndexRepository
    from app.services.document_index_service import (
        _ExecutionRepository, delete_index_record, execute_index_document,
    )
    from app.tasks.index_task import _process_cleanup_tasks_async
    import app.rag.vector_store as vector_module

    owner = str(uuid.uuid4())
    content = "V01 acceptance cancellation body"
    md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    path = tmp_path / "v01-cancel.txt"
    path.write_text(content, encoding="utf-8")
    async with mysql_sessions() as session:
        doc = await DocumentIndexRepository(session).create(
            user_id=owner, filename="v01-cancel.txt",
            original_filename="v01-cancel.txt", file_path=str(path),
            md5=md5, file_size=len(content), file_type="txt",
            status=DocumentIndexStatus.PENDING_INDEX,
        )
        doc_id = doc.id
        await session.commit()

    real = vector_module.VectorStoreService()
    entered, release, finished = (threading.Event() for _ in range(3))
    original_add = real.vectors_store.add_documents

    def gated_add(docs, ids=None, **kwargs):
        entered.set()
        try:
            assert release.wait(timeout=15), "late writer was not released"
            return original_add(docs, ids=ids, **kwargs)
        finally:
            finished.set()

    real.vectors_store.add_documents = gated_add
    monkeypatch.setattr(vector_module, "VectorStoreService", lambda: real)

    task = asyncio.ensure_future(execute_index_document(doc_id, owner))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait, 15), 20)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with mysql_sessions() as session:
            execution = (await session.execute(select(DocumentIndexExecution).where(
                DocumentIndexExecution.document_id == doc_id))).scalar_one()
            assert execution.status == ExecutionStatus.UNKNOWN
            execution_id, generation = execution.execution_id, execution.generation

        deleted = await delete_index_record(doc_id, owner, delete_file=False)
        assert deleted["state"] == "deleted"
        await _process_cleanup_tasks_async(50)
        async with mysql_sessions() as session:
            cleanup = (await session.execute(select(DocumentCleanupTask).where(
                DocumentCleanupTask.document_id == doc_id))).scalar_one()
            assert cleanup.status != "done"
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 15)
    assert finished.is_set()

    # The late vector is now persistent on disk under the deleted resource.
    late_ids = [f"{owner}:{doc_id}:g{generation}:0"]
    late = await asyncio.to_thread(read_in_fresh_process, late_ids)
    assert late["ids"] == late_ids
    await _process_cleanup_tasks_async(50)
    async with mysql_sessions() as session:
        cleanup = (await session.execute(select(DocumentCleanupTask).where(
            DocumentCleanupTask.document_id == doc_id))).scalar_one()
        assert cleanup.status != "done"

    async with mysql_sessions() as session:
        assert await _ExecutionRepository(session).resolve_unknown(
            execution_id, "writer thread finished; verified by real Chroma read")
        await session.execute(update(DocumentCleanupTask).where(
            DocumentCleanupTask.document_id == doc_id,
        ).values(status="pending", next_retry_at=None, claim_token=None,
                 lease_expires_at=None))
        await session.commit()
    for _ in range(3):
        await _process_cleanup_tasks_async(50)
        async with mysql_sessions() as session:
            status = (await session.execute(select(DocumentCleanupTask.status).where(
                DocumentCleanupTask.document_id == doc_id))).scalar_one()
        if status == "done":
            break
    assert status == "done"
    after = await asyncio.to_thread(read_in_fresh_process, late_ids)
    assert after["ids"] == []


@pytest.mark.asyncio
async def test_v03_late_vector_is_removed_by_a_second_cleanup_pass(
        mysql_sessions, monkeypatch):
    """A vector written after cleanup finished must still be removed precisely."""
    from app.models.cleanup_task import DocumentCleanupTask
    from app.tasks.index_task import _process_cleanup_tasks_async
    import app.rag.vector_store as vector_module

    owner, doc_id = str(uuid.uuid4()), str(uuid.uuid4())
    vector_id = f"{owner}:{doc_id}:g1:0"
    real = vector_module.VectorStoreService()
    monkeypatch.setattr(vector_module, "VectorStoreService", lambda: real)
    store = real.vectors_store
    await asyncio.to_thread(store.add_documents, [
        Document(page_content="late vector", metadata={
            "user_id": owner, "document_id": doc_id,
            "generation": 1, "md5": "9" * 32,
        })], ids=[vector_id])

    async def add_task(generation):
        async with mysql_sessions() as session:
            task = await CleanupTaskRepository(session).create_task(
                document_id=doc_id, user_id=owner, md5="9" * 32,
                generation=generation, reason="document_deleted")
            await session.commit()
            return task.id

    await add_task(0)
    await _process_cleanup_tasks_async(50)
    assert (await asyncio.to_thread(read_in_fresh_process, [vector_id]))["ids"] == []

    # A late writer recreates the vector after the first cleanup converged.
    await asyncio.to_thread(store.add_documents, [
        Document(page_content="late vector again", metadata={
            "user_id": owner, "document_id": doc_id,
            "generation": 1, "md5": "9" * 32,
        })], ids=[vector_id])
    await add_task(1)
    await _process_cleanup_tasks_async(50)
    assert (await asyncio.to_thread(read_in_fresh_process, [vector_id]))["ids"] == []
    async with mysql_sessions() as session:
        statuses = (await session.execute(select(DocumentCleanupTask.status).where(
            DocumentCleanupTask.document_id == doc_id))).scalars().all()
    assert statuses and all(status == "done" for status in statuses)
