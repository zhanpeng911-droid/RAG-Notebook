"""Independent V2 acceptance probes; in-memory fixtures, no live services.

These assertions express the taskbook's required behavior. A failing probe is
review evidence and must not be counted as an accepted feature.
"""
if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    probe_path = Path(__file__).resolve()
    backend_path = probe_path.parents[3] / "backend"
    os.chdir(backend_path)
    sys.path.insert(0, str(backend_path))
    os.environ.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "ENV": "test",
        "SECRET_KEY": "isolated-review-key-not-for-production-2026",
        "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "1",
        "MYSQL_USER": "isolated_test", "MYSQL_PASSWORD": "isolated_test",
        "MYSQL_DATABASE": "isolated_test",
        "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "1", "REDIS_PASSWORD": "",
        "REDIS_CACHE_URL": "", "JWT_BLACKLIST_REDIS_URL": "",
        "CELERY_BROKER_URL": "redis://127.0.0.1:1/15",
        "CELERY_RESULT_BACKEND": "redis://127.0.0.1:1/15",
        "LLM_TYPE": "OLLAMA", "VISION_MODEL_TYPE": "OPENAI",
        "OPENAI_API_KEY": "isolated-test-not-a-real-key",
        "OPENAI_API_BASE": "http://127.0.0.1:1/v1",
    })
    import pytest as runner
    raise SystemExit(runner.main([
        "-p", "tests.conftest", str(probe_path), "-q", "--runxfail",
        "--no-cov", "-p", "no:cacheprovider", "--tb=short",
    ]))

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.test_note_graph import A, B, C, OWNER, _add_note, factory  # noqa: F401
from tests.test_knowledge_insights import (
    DOC_ID, OWNER as INSIGHT_OWNER, _auth, _FakeChatModel, _llm_payload,
    client, seeded,  # noqa: F401
)
from app.models.note import Note
from app.models.note_graph import NoteLinkOccurrence
from app.models.knowledge_insight import KnowledgeArtifact
from app.services.note_link_service import remove_note_links, sync_note_links
from app.services.note_graph_service import get_note_graph
from app.services.insight_generation_service import (
    apply_mindmap_edits, build_mindmap, queue_summary_generation, run_summary_job,
)


@pytest.mark.asyncio
async def test_title_link_binding_survives_rename_and_source_resave(factory):
    await _add_note(factory, B, "Target", "original target")
    await _add_note(factory, A, "Source", "See [[Target]].")
    async with factory() as session:
        target = await session.get(Note, B)
        target.title = "Renamed"
        await session.commit()
    await _add_note(factory, C, "Target", "different note with the old title")
    async with factory() as session:
        source = await session.get(Note, A)
        source.content += "\n\nUnrelated edit."
        await session.flush()
        await sync_note_links(session, source)
        await session.commit()
        occurrence = (await session.execute(select(NoteLinkOccurrence).where(
            NoteLinkOccurrence.source_note_id == A))).scalar_one()
        assert occurrence.target_note_id == B, "Saving must not retarget an existing link to another note."


@pytest.mark.asyncio
async def test_deleted_title_target_does_not_rebind_on_source_resave(factory):
    await _add_note(factory, B, "Target", "original")
    await _add_note(factory, A, "Source", "[[Target]]")
    async with factory() as session:
        await remove_note_links(session, B, OWNER)
        await session.delete(await session.get(Note, B))
        await session.commit()
    await _add_note(factory, C, "Target", "new unrelated note")
    async with factory() as session:
        source = await session.get(Note, A)
        source.content += "\nUnrelated edit."
        await sync_note_links(session, source)
        await session.commit()
        occurrence = (await session.execute(select(NoteLinkOccurrence).where(
            NoteLinkOccurrence.source_note_id == A))).scalar_one()
        assert occurrence.target_note_id is None, "Deleted links must not silently bind to a new same-title note."


def test_regenerated_mindmap_does_not_apply_edit_to_different_topic():
    def payload(titles):
        return {"sections": [{"title": title, "claims": []} for title in titles]}
    old = build_mindmap(title="Source", payload=payload(["Security", "Caching"]))
    edited_id = old["root"]["children"][0]["id"]
    regenerated = build_mindmap(title="Source", payload=payload(["Caching", "Security"]))
    result = apply_mindmap_edits(regenerated, [SimpleNamespace(
        node_id=edited_id, label="My security annotation", sort_order=None)])
    assert result["root"]["children"][0]["label"] == "Caching", "An edit must not attach to an unrelated positional node after regeneration."


@pytest.mark.asyncio
async def test_tag_filter_searches_matching_scope_before_page_limit(factory):
    from datetime import datetime
    await _add_note(factory, B, "Old matching note", "body", tags=["wanted"])
    async with factory() as session:
        target = await session.get(Note, B)
        target.updated_at = datetime(2000, 1, 1)
        await session.commit()
    for i in range(5):
        await _add_note(factory, f"n-{i}", f"New nonmatching {i}", "body", tags=["other"])
    async with factory() as session:
        result = await get_note_graph(session, owner_user_id=OWNER, tag="wanted", limit=1)
    assert [node["id"] for node in result["nodes"]] == [B], "A valid tag match must not disappear behind newer unrelated notes."


async def _generate_fixture(factory, seeded):
    _, anchors = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(session, owner_user_id=INSIGHT_OWNER,
            source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await run_summary_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchors)))


@pytest.mark.asyncio
async def test_refresh_exposes_inflight_regeneration(client, factory, seeded):
    await _generate_fixture(factory, seeded)
    async with factory() as session:
        job, _, _ = await queue_summary_generation(session, owner_user_id=INSIGHT_OWNER,
            source_type="document", source_id=DOC_ID, force=True)
        await session.commit()
        assert job.status == "queued"
    response = await client.get(f"/api/v1/insights/sources/document/{DOC_ID}/state",
        headers=_auth(INSIGHT_OWNER))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "queued", "Refresh must expose an in-flight regeneration while retaining the previous result."


@pytest.mark.asyncio
async def test_incomplete_coverage_is_not_reported_as_ready(client, factory, seeded):
    await _generate_fixture(factory, seeded)
    async with factory() as session:
        artifact = (await session.execute(select(KnowledgeArtifact).where(
            KnowledgeArtifact.source_id == DOC_ID,
            KnowledgeArtifact.artifact_type == "summary"))).scalar_one()
        data = dict(artifact.payload_json)
        data["meta"] = {**data.get("meta", {}), "coverage": {
            "total_blocks": 3, "analyzed_blocks": 1, "partial": True,
            "missing_blocks": ["Chapter 2", "Chapter 3"],
        }}
        artifact.payload_json = data
        await session.commit()
    response = await client.get(f"/api/v1/insights/sources/document/{DOC_ID}/state",
        headers=_auth(INSIGHT_OWNER))
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "partial", "Incomplete coverage must be surfaced as a partial result."
