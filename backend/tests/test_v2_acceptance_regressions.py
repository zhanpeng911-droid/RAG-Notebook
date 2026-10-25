"""Durable product regressions promoted from independent acceptance failures.

Fixtures use an in-memory database and fixed model output, never live services.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests import test_note_graph as graph_fixtures, test_knowledge_insights as insight_fixtures
from tests.test_note_graph import A, B, C, OWNER, _add_note
from tests.test_knowledge_insights import (
    DOC_ID, OWNER as INSIGHT_OWNER, _auth, _FakeChatModel, _llm_payload,
)

from app.models.note import Note
from app.models.note_graph import NoteLinkOccurrence
from app.models.knowledge_insight import KnowledgeArtifact, KnowledgeGenerationJob
from app.services.note_link_service import remove_note_links, sync_note_links, add_manual_relation
from app.services.note_graph_service import get_note_graph, get_note_links
from app.services.insight_generation_service import (
    apply_mindmap_edits, build_mindmap, queue_summary_generation, run_summary_job,
    mindmap_revision,
)

factory = graph_fixtures.factory
client = insight_fixtures.client
seeded = insight_fixtures.seeded


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_title_binding_survives_same_title_replacement_and_shifted_offsets(factory, deleted):
    await _add_note(factory, B, "Target", "Original")
    await _add_note(factory, A, "Source", "See [[Target]] and [[Target]].")
    async with factory() as db:
        target = await db.get(Note, B)
        if deleted:
            await remove_note_links(db, B, OWNER)
            await db.delete(target)
        else:
            target.title = "Renamed"
        await db.commit()
    await _add_note(factory, C, "Target", "Different resource")
    async with factory() as db:
        source = await db.get(Note, A)
        source.content = "Unrelated prefix.\n" + source.content
        await sync_note_links(db, source)
        await db.commit()
        rows = (await db.execute(select(NoteLinkOccurrence).where(
            NoteLinkOccurrence.source_note_id == A))).scalars().all()
        assert len(rows) == 2
        assert all(row.target_note_id == (None if deleted else B) for row in rows)
        assert all(row.status == ("target_deleted" if deleted else "resolved") for row in rows)


def test_mindmap_stable_identity_and_unmapped_edits_are_preserved():
    def tree(titles):
        return build_mindmap(title="Source", payload={"sections": [
            {"title": title, "claims": []} for title in titles]})
    old = tree(["Security", "Caching"])
    security = old["root"]["children"][0]["id"]
    edit = SimpleNamespace(node_id=security, label="My security annotation", sort_order=None)
    reordered = apply_mindmap_edits(tree(["Caching", "Security"]), [edit])
    assert [node["label"] for node in reordered["root"]["children"]] == ["Caching", edit.label]
    removed = apply_mindmap_edits(tree(["Caching"]), [edit])
    assert removed["root"]["children"][0]["label"] == "Caching"
    assert removed["edit_conflicts"][0]["label"] == edit.label
    assert mindmap_revision([], payload=old, source_version_id="v") != mindmap_revision(
        [], payload=tree(["Caching", "Security"]), source_version_id="v")


@pytest.mark.asyncio
async def test_old_link_sync_does_not_overwrite_newer_note_revision(factory):
    await _add_note(factory, B, "Target", "body")
    await _add_note(factory, C, "New target", "body")
    await _add_note(factory, A, "Source", "[[Target]]")
    async with factory() as old, factory() as current:
        stale = await old.get(Note, A)
        latest = await current.get(Note, A)
        latest.content = "[[New target]]"
        await current.commit()
        await sync_note_links(current, latest)
        await current.commit()
        assert await sync_note_links(old, stale) == 0
        await old.commit()
    async with factory() as db:
        row = (await db.execute(select(NoteLinkOccurrence).where(
            NoteLinkOccurrence.source_note_id == A))).scalar_one()
        assert row.target_note_id == C


@pytest.mark.asyncio
async def test_tag_limit_applies_to_filtered_scope_and_detail_counts_match(factory):
    await _add_note(factory, B, "Old match", "body", tags=["wanted"])
    async with factory() as db:
        (await db.get(Note, B)).updated_at = datetime(2000, 1, 1)
        await db.commit()
    for i in range(5):
        await _add_note(factory, f"n-{i}", f"New {i}", "body", tags=["unwanted"])
    async with factory() as db:
        result = await get_note_graph(db, owner_user_id=OWNER, tag="wanted", limit=1)
        assert [node["id"] for node in result["nodes"]] == [B]
        assert result["counts"]["total_notes"] == 1
        assert result["counts"]["truncated"] is False
        await add_manual_relation(db, owner_user_id=OWNER, source_note_id=B, target_note_id="n-0")
        await db.commit()
        graph = await get_note_graph(db, owner_user_id=OWNER)
        detail = await get_note_links(db, owner_user_id=OWNER, note_id=B)
        node = next(node for node in graph["nodes"] if node["id"] == B)
        for key in ("degree", "out_degree", "in_degree", "manual_count"):
            assert detail["note"][key] == node[key]


async def _generated(factory, seeded):
    _, anchors = seeded
    async with factory() as db:
        job, _, _ = await queue_summary_generation(db, owner_user_id=INSIGHT_OWNER,
            source_type="document", source_id=DOC_ID)
        await db.commit()
        job_id = job.id
    await run_summary_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchors)))
    return job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running", "failed"])
async def test_regeneration_status_is_separate_from_existing_result(client, factory, seeded, status):
    await _generated(factory, seeded)
    async with factory() as db:
        job, _, _ = await queue_summary_generation(db, owner_user_id=INSIGHT_OWNER,
            source_type="document", source_id=DOC_ID, force=True)
        job.status = status
        await db.commit()
    response = await client.get(f"/api/v1/insights/sources/document/{DOC_ID}/state", headers=_auth(INSIGHT_OWNER))
    data = response.json()["data"]
    assert data["status"] == status
    assert data["result_available"] is True
    assert data["result_status"] == "ready"


@pytest.mark.asyncio
async def test_partial_coverage_is_consistent_in_state_and_recent(client, factory, seeded):
    job_id = await _generated(factory, seeded)
    async with factory() as db:
        artifact = (await db.execute(select(KnowledgeArtifact).where(
            KnowledgeArtifact.source_id == DOC_ID,
            KnowledgeArtifact.artifact_type == "summary"))).scalar_one()
        artifact.payload_json = {**artifact.payload_json, "meta": {"coverage": {
            "total_blocks": 3, "analyzed_blocks": 1, "partial": True,
            "missing_blocks": ["2", "3"]}}}
        (await db.get(KnowledgeGenerationJob, job_id)).status = "succeeded"
        await db.commit()
    state = (await client.get(f"/api/v1/insights/sources/document/{DOC_ID}/state", headers=_auth(INSIGHT_OWNER))).json()["data"]
    assert state["status"] == state["result_status"] == "partial"
    assert state["coverage"]["analyzed_blocks"] == 1
    recent = (await client.get("/api/v1/insights/recent", headers=_auth(INSIGHT_OWNER))).json()["data"]
    assert recent["items"][0]["status"] == "partial"
