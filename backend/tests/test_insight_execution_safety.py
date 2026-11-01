"""Execution ownership regressions; these use component-level SQLite fixtures."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.knowledge_insight import (
    JobStatus, KnowledgeArtifact, KnowledgeEvidence, KnowledgeGenerationJob,
    KnowledgeSourceVersion,
)
from app.repositories.knowledge_insight_repository import KnowledgeInsightRepository
from app.services.insight_generation_service import (
    SourceUnavailable, _build_verified_payload, _verify_anchor_ids, claim_and_run_job,
    parse_llm_payload, queue_summary_generation,
)
from app.tasks.insight_task import _reclaim_stale_jobs
from tests import test_knowledge_insights as insight_fixtures
from tests.test_knowledge_insights import (
    DOC_ID, OWNER, OTHER, _FakeChatModel, _llm_payload,
)

factory = insight_fixtures.factory
seeded = insight_fixtures.seeded


class BlockingModel(_FakeChatModel):
    def __init__(self, payload):
        super().__init__(payload)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().ainvoke(messages)


async def _queue(factory):
    async with factory() as db:
        job, _, _ = await queue_summary_generation(
            db, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await db.commit()
        return job.id


@pytest.mark.asyncio
async def test_running_force_is_rejected_and_duplicate_delivery_only_calls_model_once(factory, seeded):
    _, anchors = seeded
    job_id = await _queue(factory)
    model = BlockingModel(_llm_payload(anchors))
    running = asyncio.create_task(claim_and_run_job(job_id, chat_model=model))
    try:
        await asyncio.wait_for(model.entered.wait(), 5)
        assert await claim_and_run_job(job_id, chat_model=model) is False
        async with factory() as db:
            with pytest.raises(SourceUnavailable, match="generation_running"):
                await queue_summary_generation(db, owner_user_id=OWNER,
                    source_type="document", source_id=DOC_ID, force=True)
        assert model.calls == 1
    finally:
        model.release.set()
        await running
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.SUCCEEDED
        assert job.attempts == 1


@pytest.mark.asyncio
async def test_reclaimed_call_cannot_publish(factory, seeded):
    _, anchors = seeded
    job_id = await _queue(factory)
    model = BlockingModel(_llm_payload(anchors))
    running = asyncio.create_task(claim_and_run_job(job_id, chat_model=model))
    try:
        await asyncio.wait_for(model.entered.wait(), 5)
        async with factory() as db:
            job = await db.get(KnowledgeGenerationJob, job_id)
            job.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
            await db.commit()
        assert await _reclaim_stale_jobs(10) == 1
        async with factory() as db:
            job = await db.get(KnowledgeGenerationJob, job_id)
            assert job.status == JobStatus.FAILED
            assert job.error == "execution_timeout"
        model.release.set()
        await running
        async with factory() as db:
            job = await db.get(KnowledgeGenerationJob, job_id)
            assert job.status == JobStatus.FAILED
            assert job.error == "execution_timeout"
            assert (await db.execute(select(KnowledgeArtifact))).scalars().all() == []
            assert (await db.execute(select(KnowledgeEvidence))).scalars().all() == []
    finally:
        model.release.set()
        await running


@pytest.mark.asyncio
async def test_reclaimed_call_cannot_overwrite_a_new_success(factory, seeded):
    _, anchors = seeded
    job_id = await _queue(factory)
    old_model = BlockingModel(_llm_payload(anchors))
    old = asyncio.create_task(claim_and_run_job(job_id, chat_model=old_model))
    try:
        await asyncio.wait_for(old_model.entered.wait(), 5)
        async with factory() as db:
            job = await db.get(KnowledgeGenerationJob, job_id)
            job.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
            await db.commit()
        assert await _reclaim_stale_jobs(10) == 1
        await _queue(factory)
        assert await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchors)))
        async with factory() as db:
            before = [(a.id, a.payload_json) for a in (await db.execute(
                select(KnowledgeArtifact))).scalars().all()]
        old_model.release.set()
        await old
        async with factory() as db:
            job = await db.get(KnowledgeGenerationJob, job_id)
            after = [(a.id, a.payload_json) for a in (await db.execute(
                select(KnowledgeArtifact))).scalars().all()]
            assert job.status == JobStatus.SUCCEEDED
            assert job.attempts == 2
            assert after == before
    finally:
        old_model.release.set()
        await old


@pytest.mark.asyncio
async def test_model_execution_budget_marks_failure(factory, seeded, monkeypatch):
    import app.services.insight_generation_service as service

    _, anchors = seeded
    job_id = await _queue(factory)
    model = BlockingModel(_llm_payload(anchors))
    monkeypatch.setattr(service, "INSIGHT_EXECUTION_BUDGET_SECONDS", 0.2)
    running = asyncio.create_task(claim_and_run_job(job_id, chat_model=model))
    await asyncio.wait_for(model.entered.wait(), 5)
    assert not running.done()
    await asyncio.wait_for(running, 5)
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.FAILED
        assert job.error == "execution_timeout"
        assert (await db.execute(select(KnowledgeArtifact))).scalars().all() == []


@pytest.mark.asyncio
async def test_execution_budget_includes_source_preparation(factory, seeded, monkeypatch):
    import app.services.insight_generation_service as service

    job_id = await _queue(factory)
    entered = asyncio.Event()

    async def slow_title(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_load_source_title", slow_title)
    monkeypatch.setattr(service, "INSIGHT_EXECUTION_BUDGET_SECONDS", 0.2)
    running = asyncio.create_task(claim_and_run_job(job_id, chat_model=_FakeChatModel({})))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.wait_for(asyncio.shield(running), 1)
        async with factory() as db:
            job = await db.get(KnowledgeGenerationJob, job_id)
            assert job.status == JobStatus.FAILED
            assert job.error == "execution_timeout"
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_queued_dispatch_gap_is_retried_without_changing_job_state(factory, seeded, monkeypatch):
    from app.tasks import insight_task

    job_id = await _queue(factory)
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
        await db.commit()
    deliveries = []

    def send(value):
        deliveries.append(value)

    monkeypatch.setattr(insight_task.generate_insight_task, "delay", send)
    assert await insight_task._dispatch_queued_jobs(10) == 1
    assert deliveries == [job_id]
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.QUEUED
        assert job.attempts == 0
    _, anchors = seeded
    assert await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchors)))
    assert await insight_task._dispatch_queued_jobs(10) == 0


@pytest.mark.asyncio
async def test_queued_compensation_broker_error_leaves_job_retryable(factory, seeded, monkeypatch):
    from app.tasks import insight_task

    job_id = await _queue(factory)
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
        await db.commit()

    def fail(_job_id):
        raise ConnectionError("test broker unavailable")

    monkeypatch.setattr(insight_task.generate_insight_task, "delay", fail)
    assert await insight_task._dispatch_queued_jobs(10) == 0
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.QUEUED
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
        await db.commit()
    deliveries = []
    monkeypatch.setattr(insight_task.generate_insight_task, "delay", deliveries.append)
    assert await insight_task._dispatch_queued_jobs(10) == 1
    assert deliveries == [job_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_queued_compensation_rotates_oldest_batch(factory, seeded, monkeypatch, fail):
    from app.tasks import insight_task
    from tests.test_knowledge_insights import _seed_document, _seed_version

    first = await _queue(factory)
    second_doc = "doc-second-fairness"
    await _seed_document(factory, user_id=OTHER, doc_id=second_doc)
    await _seed_version(factory, user_id=OTHER, doc_id=second_doc)
    async with factory() as db:
        second, _, _ = await queue_summary_generation(
            db, owner_user_id=OTHER, source_type="document", source_id=second_doc)
        second_id = second.id
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        (await db.get(KnowledgeGenerationJob, first)).updated_at = now - timedelta(minutes=3)
        second.updated_at = now - timedelta(minutes=2)
        await db.commit()
    deliveries = []

    def send(job_id):
        deliveries.append(job_id)
        if fail:
            raise ConnectionError("test broker unavailable")

    monkeypatch.setattr(insight_task.generate_insight_task, "delay", send)
    await insight_task._dispatch_queued_jobs(1)
    await insight_task._dispatch_queued_jobs(1)
    assert deliveries == [first, second_id]
    async with factory() as db:
        assert (await db.get(KnowledgeGenerationJob, first)).status == JobStatus.QUEUED
        assert (await db.get(KnowledgeGenerationJob, second_id)).status == JobStatus.QUEUED


def test_external_reference_is_not_rebound_to_local_anchor():
    from types import SimpleNamespace

    anchor = SimpleNamespace(id="local", quote_text="Exact original text")
    assert _verify_anchor_ids(["foreign"], "Exact original text", {"local": anchor}, [anchor]) == []


def test_reference_without_quote_only_attests_anchor_identity():
    from types import SimpleNamespace

    anchor = SimpleNamespace(id="local", quote_text="Original text", quote_hash="hash")
    parsed = parse_llm_payload('{"overview":{"text":"A claim","anchor_ids":["local"]}}')
    payload, evidence, _ = _build_verified_payload(parsed, [anchor])
    assert evidence[0]["verification_status"] == "anchor_verified"
    assert evidence[0]["supporting_quote"] is None
    # Location-only evidence must not be presented as a quote-verified result.
    assert payload["trust_status"] == "anchor_verified"
    assert payload["meta"]["located_targets"] == 1
    assert payload["meta"]["verified_targets"] == 0
    assert payload["meta"]["anchor_only_targets"] == 1


def test_quoted_reference_marks_semantic_verification():
    from types import SimpleNamespace

    anchor = SimpleNamespace(id="local", quote_text="Original text", quote_hash="hash")
    parsed = parse_llm_payload(
        '{"overview":{"text":"A claim","anchor_ids":["local"],"quote":"Original text"}}')
    payload, evidence, _ = _build_verified_payload(parsed, [anchor])
    assert evidence[0]["verification_status"] == "verified"
    assert payload["trust_status"] == "verified"
    assert payload["meta"]["verified_targets"] == 1
    assert payload["meta"]["anchor_only_targets"] == 0


@pytest.mark.asyncio
async def test_reclaim_grace_does_not_steal_a_live_budget(factory, seeded, monkeypatch):
    import app.services.insight_generation_service as service
    from app.tasks import insight_task

    monkeypatch.setattr(service, "INSIGHT_EXECUTION_BUDGET_SECONDS", 10)
    monkeypatch.setattr(service, "INSIGHT_RECLAIM_GRACE_SECONDS", 5)
    job_id = await _queue(factory)
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        job.status = JobStatus.RUNNING
        job.attempts = 1
        job.started_at = now - timedelta(seconds=12)
        await db.commit()
    # 12s > budget, but still inside budget + grace; the worker may legitimately
    # be finishing setup/publication and must not be reclaimed yet.
    assert await insight_task._reclaim_stale_jobs(10) == 0
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=16)
        await db.commit()
    assert await insight_task._reclaim_stale_jobs(10) == 1
    async with factory() as db:
        assert (await db.get(KnowledgeGenerationJob, job_id)).status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_source_version_switch_blocks_late_publication(factory, seeded):
    """F05: a generation switch after the model read ACTIVE must win."""
    _, anchors = seeded
    job_id = await _queue(factory)
    model = BlockingModel(_llm_payload(anchors))
    running = asyncio.create_task(claim_and_run_job(job_id, chat_model=model))
    try:
        await asyncio.wait_for(model.entered.wait(), 5)
        async with factory() as db:
            repo = KnowledgeInsightRepository(db)
            newer = await repo.upsert_version(
                owner_user_id=OWNER, space_id=None, source_type="document",
                source_id=DOC_ID, generation=2, content_hash="new-generation",
                status="pending", chunk_count=1,
            )
            await repo.activate_version(newer)
            await db.commit()
        model.release.set()
        await running
    finally:
        model.release.set()
        await running
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.STALE
        assert job.error == "source_version_stale"
        assert (await db.execute(select(KnowledgeArtifact))).scalars().all() == []
        assert (await db.execute(select(KnowledgeEvidence))).scalars().all() == []
        versions = (await db.execute(select(KnowledgeSourceVersion))).scalars().all()
        assert {v.generation: v.status for v in versions} == {1: "stale", 2: "active"}
