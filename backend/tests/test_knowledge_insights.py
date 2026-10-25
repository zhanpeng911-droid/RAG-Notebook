"""N0-N3 知识工作台测试：锚点、证据核验、生成、导图、图谱与鉴权。"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from app.config.validator import get_settings
from app.models import Base
from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.models.knowledge_insight import (
    ArtifactType, EvidenceStatus, JobStatus, KnowledgeGenerationJob,
    SourceVersionStatus,
)
from app.models.note import Note
from app.repositories.knowledge_insight_repository import KnowledgeInsightRepository
from app.services.insight_generation_service import (
    InsightError, SourceUnavailable, _normalize_relation_type, _select_anchors,
    _verify_anchor_ids, build_mindmap, build_prompt, claim_and_run_job,
    ensure_source_version, parse_llm_payload, queue_summary_generation,
    run_summary_job,
)
from app.services.source_anchor_service import (
    activate_document_version, build_anchor_candidates,
    build_note_anchor_candidates, hash_quote, invalidate_document,
    invalidate_note, page_from_metadata, record_document_anchors,
    record_note_anchors, verify_anchor,
)

import app.db.db_config as db_config

SECRET = get_settings().SECRET_KEY
OWNER = "u-owner-0000-0000-000000000001"
OTHER = "u-other-0000-0000-000000000002"
DOC_ID = "doc-0000-0000-0000-000000000001"
DOC_MD5 = "a" * 32


def _token(user_id: str) -> str:
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def _source_and_chunks():
    source = [SimpleNamespace(
        page_content="# 总览\n\n第一段内容。\n\n## 细节\n\n第二段内容在这里。",
        metadata={"page": 0},
    )]
    chunks = [
        SimpleNamespace(page_content="# 总览\n\n第一段内容。", metadata={"page": 0}),
        SimpleNamespace(page_content="## 细节\n\n第二段内容在这里。", metadata={"page": 0}),
    ]
    return source, chunks


async def _seed_document(factory, *, user_id: str = OWNER,
                         doc_id: str = DOC_ID, active_generation: int = 1):
    async with factory() as session:
        session.add(DocumentIndex(
            id=doc_id,
            user_id=user_id,
            space_id=None,
            filename="f.txt",
            original_filename="示例文档.txt",
            file_path="",
            file_size=100,
            file_type=".txt",
            md5=DOC_MD5,
            status=DocumentIndexStatus.INDEXED,
            generation=active_generation,
            active_generation=active_generation,
            chunk_count=2,
        ))
        await session.commit()


async def _seed_version(factory, *, user_id: str = OWNER, doc_id: str = DOC_ID,
                        generation: int = 1, activate: bool = True):
    source, chunks = _source_and_chunks()
    async with factory() as session:
        version = await record_document_anchors(
            session,
            owner_user_id=user_id,
            space_id=None,
            document_id=doc_id,
            generation=generation,
            content_hash=DOC_MD5,
            source_documents=source,
            split_documents=chunks,
            chunk_ids=["c0", "c1"],
        )
        if activate:
            await activate_document_version(
                session, owner_user_id=user_id,
                document_id=doc_id, generation=generation)
        await session.commit()
        repo = KnowledgeInsightRepository(session)
        anchors = await repo.list_anchors(version.id)
        return version.id, [anchor.id for anchor in anchors]


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_config, "AsyncSessionLocal", session_factory)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(factory):
    await _seed_document(factory)
    version_id, anchor_ids = await _seed_version(factory)
    return version_id, anchor_ids


# ---------------------------------------------------------------------------
# N0: anchors
# ---------------------------------------------------------------------------

def test_page_from_metadata_never_fabricates_page():
    assert page_from_metadata({"page": 0}) == 1
    assert page_from_metadata({"page_number": 3}) == 3
    assert page_from_metadata({"slide_number": 2}) == 2
    assert page_from_metadata({}) is None
    assert page_from_metadata(None) is None


def test_anchor_candidates_page_heading_and_offsets():
    source, chunks = _source_and_chunks()
    candidates = build_anchor_candidates(source, chunks, ["c0", "c1"])
    assert [c.chunk_id for c in candidates] == ["c0", "c1"]
    assert [c.page_no for c in candidates] == [1, 1]
    assert candidates[0].heading_path == "总览"
    assert candidates[1].heading_path == "总览 > 细节"
    assert candidates[0].char_start == 0
    assert candidates[1].char_start > candidates[0].char_start
    assert candidates[0].quote_hash == hash_quote(candidates[0].quote_text)
    assert candidates[0].locator_kind == "page"
    assert candidates[0].extraction_confidence == 1.0


def test_anchor_candidates_without_source_do_not_claim_page_or_offsets():
    chunks = [
        SimpleNamespace(page_content="一段没有元数据的内容", metadata={}),
    ]
    candidates = build_anchor_candidates([], chunks)
    assert candidates[0].page_no is None
    assert candidates[0].char_start is None
    assert candidates[0].locator_kind == "chunk"
    assert candidates[0].extraction_confidence == 0.4


def test_note_anchors_have_offsets_but_no_page():
    candidates = build_note_anchor_candidates("# 标题\n\n第一段。\n\n第二段。")
    assert candidates
    assert all(c.page_no is None for c in candidates)
    assert candidates[0].char_start == 0
    assert candidates[0].heading_path == "标题"


# ---------------------------------------------------------------------------
# N0: version activation / verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_and_activate_document_version(factory):
    await _seed_document(factory)
    version_id, anchor_ids = await _seed_version(factory)
    assert len(anchor_ids) == 2
    async with factory() as session:
        repo = KnowledgeInsightRepository(session)
        version = await repo.get_version_by_id(version_id)
        assert version.status == SourceVersionStatus.ACTIVE
        anchors = await repo.list_anchors(version_id)
        assert {a.owner_user_id for a in anchors} == {OWNER}


@pytest.mark.asyncio
async def test_verify_anchor_owner_generation_and_quote(factory):
    await _seed_document(factory)
    _, anchor_ids = await _seed_version(factory)
    correct_quote = "第二段内容"

    async with factory() as session:
        ok = await verify_anchor(
            session, owner_user_id=OWNER, anchor_id=anchor_ids[1],
            quote=correct_quote)
        assert ok.verified is True

        wrong = await verify_anchor(
            session, owner_user_id=OWNER, anchor_id=anchor_ids[1],
            quote="完全不存在的引文")
        assert wrong.verified is False
        assert wrong.reason == "quote_mismatch"

        forbidden = await verify_anchor(
            session, owner_user_id=OTHER, anchor_id=anchor_ids[1])
        assert forbidden.verified is False
        assert forbidden.reason == "forbidden"
        assert forbidden.anchor is None

        document = await session.get(DocumentIndex, DOC_ID)
        document.active_generation = 2
        await session.commit()

        superseded = await verify_anchor(
            session, owner_user_id=OWNER, anchor_id=anchor_ids[1])
        assert superseded.verified is False
        assert superseded.reason == "generation_superseded"


@pytest.mark.asyncio
async def test_new_generation_activation_invalidates_old_artifact(factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        repo = KnowledgeInsightRepository(session)
        version1 = await repo.get_version_by_id(version_id)
        artifact = await repo.upsert_artifact(
            owner_user_id=OWNER,
            version=version1,
            artifact_type=ArtifactType.SUMMARY,
            schema_version="1.0",
            payload={"overview": {"text": "旧", "anchor_ids": anchor_ids}},
        )
        await session.commit()
        assert artifact.status == "active"

    version2_id, _ = await _seed_version(factory, generation=2, activate=False)
    async with factory() as session:
        document = await session.get(DocumentIndex, DOC_ID)
        document.active_generation = 2
        document.generation = 2
        await session.commit()
        await activate_document_version(
            session, owner_user_id=OWNER, document_id=DOC_ID, generation=2)
        await session.commit()

        repo = KnowledgeInsightRepository(session)
        assert (await repo.get_version_by_id(version_id)).status == SourceVersionStatus.STALE
        assert (await repo.get_version_by_id(version2_id)).status == SourceVersionStatus.ACTIVE
        assert (await repo.get_artifact_by_version(version_id, "summary")).status == "stale"


# ---------------------------------------------------------------------------
# N1/N2: generation
# ---------------------------------------------------------------------------

def _llm_payload(anchor_ids):
    return {
        "overview": {"text": "这是全文概览。", "anchor_ids": [anchor_ids[0]]},
        "key_points": [
            {"text": "可信结论", "anchor_ids": [anchor_ids[0]]},
            {"text": "伪造结论", "anchor_ids": ["not-a-real-anchor"]},
        ],
        "sections": [{
            "title": "细节",
            "summary": "章节摘要",
            "anchor_ids": [anchor_ids[1]],
            "claims": [{
                "text": "章节结论",
                "anchor_ids": [anchor_ids[1]],
                "quote": "第二段内容在这里",
            }],
        }],
        "terms": [
            {"name": "RAG", "description": "检索增强生成",
             "anchor_ids": [anchor_ids[0]], "aliases": ["检索增强"]},
            {"name": "Embedding", "description": "向量表示",
             "anchor_ids": [anchor_ids[1]]},
        ],
        "open_questions": [{"text": "还需要确认的问题",
                            "anchor_ids": [anchor_ids[0]]}],
        "relations": [
            {"source": "RAG", "target": "Embedding",
             "relation_type": "depends_on", "anchor_ids": [anchor_ids[1]]},
            {"source": "RAG", "target": "不存在的概念",
             "relation_type": "related_to", "anchor_ids": [anchor_ids[0]]},
        ],
    }


class _FakeChatModel:
    def __init__(self, payload):
        self.payload = payload

    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


class _BrokenChatModel:
    async def ainvoke(self, messages):
        return SimpleNamespace(content="这不是 JSON")


@pytest.mark.asyncio
async def test_generation_filters_unverified_and_builds_mindmap_and_graph(factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        job, already, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document",
            source_id=DOC_ID)
        await session.commit()
        job_id = job.id
        assert already is False
        assert job.status == JobStatus.QUEUED

    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    async with factory() as session:
        repo = KnowledgeInsightRepository(session)
        artifact = await repo.get_artifact(
            owner_user_id=OWNER, source_type="document", source_id=DOC_ID,
            artifact_type="summary")
        assert artifact is not None
        payload = artifact.payload_json
        assert payload["trust_status"] == "verified"
        assert payload["overview"]["text"] == "这是全文概览。"
        assert [kp["text"] for kp in payload["key_points"]] == ["可信结论"]
        assert payload["sections"][0]["claims"][0]["anchor_ids"] == [anchor_ids[1]]
        assert len(payload["terms"]) == 2
        assert len(payload["relations"]) == 1
        assert payload["relations"][0]["relation_type"] == "depends_on"
        assert payload["meta"]["dropped_unverified"] >= 2

        evidence = await repo.list_evidence(artifact.id)
        assert evidence
        assert all(row.verification_status == EvidenceStatus.VERIFIED for row in evidence)
        kinds = {row.target_kind for row in evidence}
        assert {"summary", "key_point", "section", "term", "open_question"} <= kinds

        mindmap = await repo.get_artifact_by_version(version_id, "mindmap")
        assert mindmap is not None
        tree = mindmap.payload_json
        assert tree["node_count"] >= 3
        assert tree["node_count"] <= 100
        assert tree["max_depth"] <= 3
        assert tree["root"]["children"][0]["children"][0]["anchor_ids"]

        concepts = await repo.list_concepts(owner_user_id=OWNER, space_key="")
        assert {c.canonical_name for c in concepts} == {"RAG", "Embedding"}
        relations = await repo.list_relations(owner_user_id=OWNER, space_key="")
        assert len(relations) == 1
        rel_evidence = await repo.list_active_evidence_for_relations([relations[0].id])
        assert rel_evidence


@pytest.mark.asyncio
async def test_generation_marks_insufficient_evidence_instead_of_faking(factory, seeded):
    _, anchor_ids = seeded
    payload = {
        "overview": {"text": "没有证据的概览", "anchor_ids": ["missing"]},
        "key_points": [{"text": "没有证据的结论", "anchor_ids": ["missing"]}],
        "sections": [],
        "terms": [],
        "open_questions": [],
        "relations": [],
    }
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id

    await claim_and_run_job(job_id, chat_model=_FakeChatModel(payload))
    async with factory() as session:
        artifact = await KnowledgeInsightRepository(session).get_artifact(
            owner_user_id=OWNER, source_type="document", source_id=DOC_ID,
            artifact_type="summary")
        assert artifact.payload_json["trust_status"] == "insufficient_evidence"
        assert artifact.payload_json["overview"]["text"] == ""
        assert artifact.payload_json["key_points"] == []
        mindmap = await KnowledgeInsightRepository(session).get_artifact_by_version(
            artifact.source_version_id, "mindmap")
        assert mindmap.payload_json["node_count"] == 1


@pytest.mark.asyncio
async def test_generation_failure_is_recorded(factory, seeded):
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id

    with pytest.raises(InsightError):
        await claim_and_run_job(job_id, chat_model=_BrokenChatModel())

    async with factory() as session:
        job = await session.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.FAILED
        assert "llm_output" in (job.error or "")


@pytest.mark.asyncio
async def test_generation_requires_owner(factory):
    await _seed_document(factory)
    async with factory() as session:
        with pytest.raises(SourceUnavailable):
            await queue_summary_generation(
                session, owner_user_id=OTHER, source_type="document",
                source_id=DOC_ID)


# ---------------------------------------------------------------------------
# N0: notes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_note_versioning_and_invalidation(factory):
    async with factory() as session:
        session.add(Note(id="note-1", user_id=OWNER, title="笔记", content="# 标题\n\n第一版"))
        await session.commit()

        first = await record_note_anchors(
            session, note_id="note-1", owner_user_id=OWNER,
            note_text="# 标题\n\n第一版")
        await session.commit()
        first_id = first.id
        first_generation = first.generation

        second = await record_note_anchors(
            session, note_id="note-1", owner_user_id=OWNER,
            note_text="# 标题\n\n第二版\n\n更多内容")
        await session.commit()

        repo = KnowledgeInsightRepository(session)
        old = await repo.get_version_by_id(first_id)
        assert old.status == SourceVersionStatus.STALE
        assert second.generation == first_generation + 1
        assert second.status == SourceVersionStatus.ACTIVE

        await invalidate_note(session, owner_user_id=OWNER, note_id="note-1")
        await session.commit()
        refreshed = await repo.get_version_by_id(second.id)
        assert refreshed.status == SourceVersionStatus.INVALID


@pytest.mark.asyncio
async def test_note_insight_generation(client, factory):
    async with factory() as session:
        session.add(Note(id="note-gen", user_id=OWNER, title="笔记洞察",
                         content="# 标题\n\n第一段内容。\n\n## 细节\n\n第二段内容。"))
        await session.commit()
        version = await record_note_anchors(
            session, note_id="note-gen", owner_user_id=OWNER,
            note_text="# 标题\n\n第一段内容。\n\n## 细节\n\n第二段内容。")
        await session.commit()
        repo = KnowledgeInsightRepository(session)
        anchor_ids = [a.id for a in await repo.list_anchors(version.id)]
        job, already, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="note", source_id="note-gen")
        await session.commit()
        job_id = job.id
        assert already is False

    padded_anchor_ids = anchor_ids if len(anchor_ids) > 1 else anchor_ids * 2
    await claim_and_run_job(
        job_id, chat_model=_FakeChatModel(_llm_payload(padded_anchor_ids)))

    response = await client.get("/api/v1/insights/notes/note-gen", headers=_auth(OWNER))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ready"
    assert data["title"] == "笔记洞察"
    assert data["artifact"]["overview"]["text"]

    other = await client.get("/api/v1/insights/notes/note-gen", headers=_auth(OTHER))
    assert other.status_code == 404


# ---------------------------------------------------------------------------
# API + graph isolation
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(factory, monkeypatch):
    from fastapi import FastAPI
    from app.core.failed_response_register import register_exception_handlers
    from app.db.db_config import get_db
    from app.router.insight_router import insight_router
    import app.router.insight_router as insight_router_module

    app = FastAPI()
    app.include_router(insight_router, prefix="/api/v1")
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    dispatched = []
    monkeypatch.setattr(
        insight_router_module, "dispatch_insight_job",
        lambda job_id: dispatched.append(job_id))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.dispatched = dispatched
        yield c


@pytest.mark.asyncio
async def test_api_insight_evidence_and_mindmap(client, factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    response = await client.get(f"/api/v1/insights/documents/{DOC_ID}", headers=_auth(OWNER))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ready"
    assert data["artifact"]["overview"]["text"]
    assert data["evidence"]["overview"][0]["anchor_id"] == anchor_ids[0]

    mind = await client.get(
        f"/api/v1/insights/documents/{DOC_ID}/mindmap", headers=_auth(OWNER))
    assert mind.status_code == 200
    assert mind.json()["data"]["mindmap"]["node_count"] >= 3

    anchor = await client.get(
        f"/api/v1/insights/anchors/{anchor_ids[1]}", headers=_auth(OWNER))
    assert anchor.status_code == 200
    assert anchor.json()["data"]["page_no"] == 1

    mismatch = await client.get(
        f"/api/v1/insights/anchors/{anchor_ids[1]}",
        params={"quote": "不存在的引文"}, headers=_auth(OWNER))
    assert mismatch.status_code == 422

    other_anchor = await client.get(
        f"/api/v1/insights/anchors/{anchor_ids[1]}", headers=_auth(OTHER))
    assert other_anchor.status_code in (403, 404)

    other_doc = await client.get(
        f"/api/v1/insights/documents/{DOC_ID}", headers=_auth(OTHER))
    assert other_doc.status_code == 404


@pytest.mark.asyncio
async def test_generate_endpoint_owner_check_and_dispatch(client, factory, seeded):
    response = await client.post(
        f"/api/v1/insights/documents/{DOC_ID}/generate",
        json={"force": False}, headers=_auth(OWNER))
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["job_id"]
    assert client.dispatched == [body["job_id"]]

    other = await client.post(
        f"/api/v1/insights/documents/{DOC_ID}/generate",
        json={"force": False}, headers=_auth(OTHER))
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_graph_hides_relations_after_source_invalidation(client, factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    graph = await client.get("/api/v1/insights/graph", headers=_auth(OWNER))
    assert graph.status_code == 200
    data = graph.json()["data"]
    assert "limits" in data
    assert len(data["edges"]) == 1
    assert len(data["nodes"]) == 2
    relation_id = data["edges"][0]["id"]

    rejected = await client.patch(
        f"/api/v1/insights/graph/relations/{relation_id}",
        json={"verification_state": "rejected"}, headers=_auth(OTHER))
    assert rejected.status_code == 404

    rejected_by_owner = await client.patch(
        f"/api/v1/insights/graph/relations/{relation_id}",
        json={"verification_state": "rejected"}, headers=_auth(OWNER))
    assert rejected_by_owner.status_code == 200
    after_reject = await client.get("/api/v1/insights/graph", headers=_auth(OWNER))
    assert after_reject.json()["data"]["edges"] == []

    confirmed = await client.patch(
        f"/api/v1/insights/graph/relations/{relation_id}",
        json={"verification_state": "confirmed"}, headers=_auth(OWNER))
    assert confirmed.status_code == 200

    async with factory() as session:
        await invalidate_document(
            session, owner_user_id=OWNER, document_id=DOC_ID, invalid=True)
        await session.commit()

    stale = await client.get("/api/v1/insights/graph", headers=_auth(OWNER))
    assert stale.status_code == 200
    assert stale.json()["data"]["edges"] == []
    assert stale.json()["data"]["nodes"] == []

    insight = await client.get(
        f"/api/v1/insights/documents/{DOC_ID}", headers=_auth(OWNER))
    assert insight.json()["data"]["status"] == "stale"

    anchor = await client.get(
        f"/api/v1/insights/anchors/{anchor_ids[0]}", headers=_auth(OWNER))
    assert anchor.status_code == 409


@pytest.mark.asyncio
async def test_graph_is_scoped_by_owner(client, factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    other = await client.get("/api/v1/insights/graph", headers=_auth(OTHER))
    assert other.status_code == 200
    assert other.json()["data"]["nodes"] == []
    assert other.json()["data"]["edges"] == []


# ---------------------------------------------------------------------------
# 分支覆盖：路由、任务回收、锚点边界与生成辅助
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_remaining_branches(client, factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    async with factory() as session:
        repo = KnowledgeInsightRepository(session)
        concepts = await repo.list_concepts(owner_user_id=OWNER, space_key="")
        relations = await repo.list_relations(owner_user_id=OWNER, space_key="")
        concept_id = concepts[0].id
        relation_id = relations[0].id
        session.add(DocumentIndex(
            id="doc-empty", user_id=OWNER, filename="empty.txt",
            original_filename="空文档.txt", file_path="", md5="b" * 32,
            status=DocumentIndexStatus.INDEXED, generation=0, active_generation=0,
            chunk_count=0,
        ))
        session.add(Note(id="note-empty", user_id=OWNER, title="空笔记", content="内容"))
        await session.commit()

    job_resp = await client.get(f"/api/v1/insights/jobs/{job_id}", headers=_auth(OWNER))
    assert job_resp.status_code == 200
    assert job_resp.json()["data"]["status"] == "succeeded"
    assert (await client.get(f"/api/v1/insights/jobs/{job_id}",
                             headers=_auth(OTHER))).status_code == 404
    assert (await client.get("/api/v1/insights/jobs/missing",
                             headers=_auth(OWNER))).status_code == 404

    node = await client.get(f"/api/v1/insights/graph/nodes/{concept_id}",
                            headers=_auth(OWNER))
    assert node.status_code == 200
    assert node.json()["data"]["evidence"]
    assert (await client.get(f"/api/v1/insights/graph/nodes/{concept_id}",
                             headers=_auth(OTHER))).status_code == 404

    relation = await client.get(f"/api/v1/insights/graph/relations/{relation_id}",
                                headers=_auth(OWNER))
    assert relation.status_code == 200
    assert relation.json()["data"]["evidence"]
    assert (await client.get(f"/api/v1/insights/graph/relations/{relation_id}",
                             headers=_auth(OTHER))).status_code == 404
    assert (await client.get("/api/v1/insights/graph/relations/missing",
                             headers=_auth(OWNER))).status_code == 404

    patched_node = await client.patch(
        f"/api/v1/insights/graph/nodes/{concept_id}",
        json={"status": "confirmed"}, headers=_auth(OWNER))
    assert patched_node.status_code == 200
    assert patched_node.json()["data"]["status"] == "confirmed"

    filtered = await client.get("/api/v1/insights/graph", params={"q": "RAG"},
                                headers=_auth(OWNER))
    assert filtered.status_code == 200
    assert isinstance(filtered.json()["data"]["nodes"], list)

    empty_insight = await client.get("/api/v1/insights/documents/doc-empty",
                                     headers=_auth(OWNER))
    assert empty_insight.json()["data"]["status"] == "not_generated"
    empty_mind = await client.get("/api/v1/insights/documents/doc-empty/mindmap",
                                  headers=_auth(OWNER))
    assert empty_mind.json()["data"]["status"] == "not_generated"

    note_insight = await client.get("/api/v1/insights/notes/note-empty",
                                    headers=_auth(OWNER))
    assert note_insight.json()["data"]["status"] == "not_generated"
    note_mind = await client.get("/api/v1/insights/notes/note-empty/mindmap",
                                 headers=_auth(OWNER))
    assert note_mind.json()["data"]["status"] == "not_generated"

    generated = await client.post(
        "/api/v1/insights/notes/note-empty/generate",
        json={"force": False}, headers=_auth(OWNER))
    assert generated.status_code == 200
    assert generated.json()["data"]["job_id"]

    missing_anchor = await client.get("/api/v1/insights/anchors/missing",
                                      headers=_auth(OWNER))
    assert missing_anchor.status_code == 404


@pytest.mark.asyncio
async def test_source_anchor_edge_cases(factory):
    source = [SimpleNamespace(
        page_content="第一段   有多个空格\n\n第二段内容", metadata={})]
    chunk = SimpleNamespace(page_content="第一段 有多个空格", metadata={})
    candidates = build_anchor_candidates(source, [chunk])
    assert candidates[0].char_start == 0
    assert candidates[0].char_end is not None
    assert candidates[0].extraction_confidence == 1.0

    heading_source = [SimpleNamespace(
        page_content="第一章\n正文内容", metadata={
            "headings": [{"title": "第一章", "level": 1, "char_start": 0}],
        })]
    heading_chunk = SimpleNamespace(page_content="正文内容", metadata={})
    heading_candidates = build_anchor_candidates(heading_source, [heading_chunk])
    assert heading_candidates[0].heading_path == "第一章"

    long_note = "\n\n".join([f"第{i}段" + "内容" * 200 for i in range(8)])
    note_candidates = build_note_anchor_candidates(long_note)
    assert len(note_candidates) > 1
    assert all(c.page_no is None for c in note_candidates)

    async with factory() as session:
        session.add(Note(id="note-verify", user_id=OWNER, title="核验", content="# 标题\n\n正文"))
        await session.commit()
        version = await record_note_anchors(
            session, note_id="note-verify", owner_user_id=OWNER,
            note_text="# 标题\n\n正文")
        await session.commit()
        repo = KnowledgeInsightRepository(session)
        anchor_id = (await repo.list_anchors(version.id))[0].id

        ok = await verify_anchor(session, owner_user_id=OWNER, anchor_id=anchor_id)
        assert ok.verified is True
        forbidden = await verify_anchor(session, owner_user_id=OTHER, anchor_id=anchor_id)
        assert forbidden.reason == "forbidden"

        await invalidate_note(session, owner_user_id=OWNER, note_id="note-verify")
        await session.commit()
        stale = await verify_anchor(session, owner_user_id=OWNER, anchor_id=anchor_id)
        assert stale.reason == "stale_version"

        # 缺失版本激活是幂等空操作
        await activate_document_version(
            session, owner_user_id=OWNER, document_id="missing", generation=9)


@pytest.mark.asyncio
async def test_invalidate_document_marks_queued_jobs_stale(factory):
    await _seed_document(factory)
    await _seed_version(factory)
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
        await invalidate_document(
            session, owner_user_id=OWNER, document_id=DOC_ID, invalid=True)
        await session.commit()
        refreshed = await session.get(KnowledgeGenerationJob, job_id)
        assert refreshed.status == JobStatus.STALE


def test_generation_helper_branches():
    valid = {"overview": {"text": "概览", "anchor_ids": []}}
    assert parse_llm_payload("```json\n" + json.dumps(valid) + "\n```").overview.text == "概览"
    with pytest.raises(InsightError):
        parse_llm_payload("not a json")
    with pytest.raises(InsightError):
        parse_llm_payload('{"overview": 1}')

    anchors = [
        SimpleNamespace(id=f"a{i}", chunk_index=i, quote_text=f"原文{i}",
                        page_no=i + 1, heading_path=None)
        for i in range(200)
    ]
    selected = _select_anchors(anchors)
    assert len(selected) <= 120
    prompt = build_prompt(title="标题", anchors=selected[:2])
    assert "[a0]" in prompt and "第1页" in prompt

    assert _normalize_relation_type("is-a") == "is_a"
    assert _normalize_relation_type("未知关系") == "related_to"

    anchor = SimpleNamespace(id="a0", quote_text="这是原文证据")
    assert _verify_anchor_ids(["a0"], "原文证据", {"a0": anchor}, [anchor]) == ["a0"]
    assert _verify_anchor_ids(["bad"], "原文证据", {"a0": anchor}, [anchor]) == ["a0"]
    assert _verify_anchor_ids(["a0"], "不匹配", {"a0": anchor}, [anchor]) == []

    payload = {
        "overview": {"text": "概览", "anchor_ids": ["a0"]},
        "key_points": [{"id": "kp-0", "text": "结论", "anchor_ids": ["a0"]}],
        "sections": [],
        "terms": [],
        "open_questions": [],
        "relations": [],
    }
    mindmap = build_mindmap(title="标题", payload=payload)
    assert mindmap["node_count"] == 2
    assert mindmap["root"]["children"][0]["label"] == "结论"


@pytest.mark.asyncio
async def test_legacy_document_anchors_are_built_on_demand(factory, tmp_path, monkeypatch):
    file_path = tmp_path / "legacy.txt"
    file_path.write_text("内容", encoding="utf-8")
    async with factory() as session:
        session.add(DocumentIndex(
            id="doc-legacy", user_id=OWNER, filename="legacy.txt",
            original_filename="旧文档.txt", file_path=str(file_path),
            md5="c" * 32, status=DocumentIndexStatus.INDEXED,
            generation=0, active_generation=0, chunk_count=0,
        ))
        await session.commit()

        source, chunks = _source_and_chunks()
        import app.services.insight_generation_service as insight_module
        monkeypatch.setattr(
            insight_module, "_load_and_split_document",
            lambda path: (source, chunks))

        version = await ensure_source_version(
            session, owner_user_id=OWNER, source_type="document",
            source_id="doc-legacy")
        await session.commit()
        assert version.status == SourceVersionStatus.ACTIVE
        assert version.generation == 0
        assert version.chunk_count == 2


@pytest.mark.asyncio
async def test_run_job_stale_and_no_anchor_paths(factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        # 无锚点版本
        repo = KnowledgeInsightRepository(session)
        empty_version = await repo.upsert_version(
            owner_user_id=OWNER, space_id=None, source_type="document",
            source_id="doc-no-anchor", generation=1, content_hash="d" * 32,
            status=SourceVersionStatus.ACTIVE, chunk_count=0)
        job = await repo.upsert_job(
            owner_user_id=OWNER, version=empty_version,
            artifact_type="summary", prompt_version="insight-v1",
            model_id=None)
        await session.commit()
        job_id = job.id

    await run_summary_job(job_id, chat_model=_FakeChatModel({}))
    async with factory() as session:
        job = await session.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.FAILED
        assert job.error == "no_source_anchors"

    # 版本在任务开始前失效
    async with factory() as session:
        repo = KnowledgeInsightRepository(session)
        version = await repo.get_version_by_id(version_id)
        job = await repo.upsert_job(
            owner_user_id=OWNER, version=version, artifact_type="mindmap",
            prompt_version="insight-v1", model_id=None)
        await session.commit()
        stale_job_id = job.id
        version.status = SourceVersionStatus.STALE
        await session.commit()

    await run_summary_job(stale_job_id, chat_model=_FakeChatModel({}))
    async with factory() as session:
        job = await session.get(KnowledgeGenerationJob, stale_job_id)
        assert job.status == JobStatus.STALE


@pytest.mark.asyncio
async def test_execute_index_document_records_and_activates_anchors(factory, monkeypatch):
    """N0 索引接线：统一执行器发布成功后锚点版本应为 active。"""
    import sys

    import app.services.document_index_service as index_service

    vector_store_module = sys.modules["app.rag.vector_store"]

    source, chunks = _source_and_chunks()

    class _FakeVectors:
        def __init__(self):
            self.ids = []

        def add_documents(self, documents, ids=None):
            self.ids = list(ids or [])

    class _FakeStore:
        def __init__(self):
            self.vectors_store = _FakeVectors()

        async def get_file_document(self, path, md5=None, user_id=None):
            return list(source)

        def split_documents_sync(self, documents):
            return list(chunks)

        async def save_md5_hex(self, *args, **kwargs):
            return None

    fake_store = _FakeStore()
    monkeypatch.setattr(vector_store_module, "VectorStoreService", lambda: fake_store)
    monkeypatch.setattr(index_service.os.path, "exists", lambda path: True)

    async with factory() as session:
        session.add(DocumentIndex(
            id=DOC_ID, user_id=OWNER, filename="f.txt",
            original_filename="示例文档.txt", file_path="/tmp/f.txt",
            file_size=100, file_type=".txt", md5=DOC_MD5,
            status=DocumentIndexStatus.PENDING_INDEX,
            generation=0, active_generation=0, chunk_count=0,
        ))
        await session.commit()

    result = await index_service.execute_index_document(DOC_ID, OWNER)
    assert result["status"] == DocumentIndexStatus.INDEXED

    async with factory() as session:
        document = await session.get(DocumentIndex, DOC_ID)
        assert document.active_generation == 1
        assert document.chunk_count == 2
        repo = KnowledgeInsightRepository(session)
        version = await repo.get_version(
            source_type="document", source_id=DOC_ID, generation=1)
        assert version.status == SourceVersionStatus.ACTIVE
        anchors = await repo.list_anchors(version.id)
        assert len(anchors) == 2
        assert anchors[0].chunk_id == fake_store.vectors_store.ids[0]


@pytest.mark.asyncio
async def test_router_functions_direct(factory, seeded, monkeypatch):
    """直接调用路由函数，补齐 ASGI 传输下异步续行未被 coverage 统计的分支。"""
    import app.router.insight_router as router_module
    from app.schemas.insight_models import InsightGenerateRequest

    version_id, anchor_ids = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    monkeypatch.setattr(router_module, "dispatch_insight_job", lambda job_id: None)
    monkeypatch.setattr(
        router_module, "ensure_space_member",
        lambda space_id, user_id, db: _async_none())

    async with factory() as session:
        repo = KnowledgeInsightRepository(session)
        concepts = await repo.list_concepts(owner_user_id=OWNER, space_key="")
        relations = await repo.list_relations(owner_user_id=OWNER, space_key="")
        concept_id = concepts[0].id
        relation_id = relations[0].id

        # 新文档：有来源版本但还没有产物 → 覆盖投递分支
        source, chunks = _source_and_chunks()
        session.add(DocumentIndex(
            id="doc-second", user_id=OWNER, filename="second.txt",
            original_filename="第二篇.txt", file_path="",
            md5="f" * 32, status=DocumentIndexStatus.INDEXED,
            generation=1, active_generation=1, chunk_count=2,
        ))
        await session.commit()
        await record_document_anchors(
            session, owner_user_id=OWNER, space_id=None,
            document_id="doc-second", generation=1, content_hash="f" * 32,
            source_documents=source, split_documents=chunks)
        await activate_document_version(
            session, owner_user_id=OWNER, document_id="doc-second", generation=1)
        session.add(Note(id="note-direct", user_id=OWNER, title="直接调用", content="内容"))
        await session.commit()

        job_resp = await router_module.get_insight_job(
            job_id, user_id=OWNER, db=session)
        assert job_resp.status_code == 200

        doc_resp = await router_module.get_document_insight(
            DOC_ID, user_id=OWNER, db=session)
        assert doc_resp.status_code == 200
        mind_resp = await router_module.get_document_mindmap(
            DOC_ID, user_id=OWNER, db=session)
        assert mind_resp.status_code == 200

        note_resp = await router_module.get_note_insight(
            "note-direct", user_id=OWNER, db=session)
        assert note_resp.status_code == 200
        note_mind = await router_module.get_note_mindmap(
            "note-direct", user_id=OWNER, db=session)
        assert note_mind.status_code == 200

        anchor_resp = await router_module.get_anchor(
            anchor_ids[0], quote=None, user_id=OWNER, db=session)
        assert anchor_resp.status_code == 200
        with pytest.raises(Exception) as mismatch:
            await router_module.get_anchor(
                anchor_ids[0], quote="不存在的引文", user_id=OWNER, db=session)
        assert getattr(mismatch.value, "status_code", None) == 422

        graph_resp = await router_module.get_graph(
            space_id=None, q=None, limit=200, user_id=OWNER, db=session)
        assert graph_resp.status_code == 200
        graph_space = await router_module.get_graph(
            space_id="space-direct", q=None, limit=200, user_id=OWNER, db=session)
        assert graph_space.status_code == 200

        node_resp = await router_module.get_graph_node(
            concept_id, user_id=OWNER, db=session)
        assert node_resp.status_code == 200
        relation_resp = await router_module.get_graph_relation(
            relation_id, user_id=OWNER, db=session)
        assert relation_resp.status_code == 200
        patched_node = await router_module.patch_graph_node(
            concept_id, router_module.ConceptPatch(status="confirmed"),
            user_id=OWNER, db=session)
        assert patched_node.status_code == 200
        patched_relation = await router_module.patch_graph_relation(
            relation_id, router_module.RelationPatch(verification_state="confirmed"),
            user_id=OWNER, db=session)
        assert patched_relation.status_code == 200

        generate_resp = await router_module.generate_document_insight(
            "doc-second", InsightGenerateRequest(force=False),
            user_id=OWNER, db=session)
        assert generate_resp.status_code == 200
        note_generate = await router_module.generate_note_insight(
            "note-direct", InsightGenerateRequest(force=False),
            user_id=OWNER, db=session)
        assert note_generate.status_code == 200

        with pytest.raises(Exception) as forbidden:
            await router_module.generate_document_insight(
                DOC_ID, InsightGenerateRequest(force=False),
                user_id=OTHER, db=session)
        assert getattr(forbidden.value, "status_code", None) == 404

        with pytest.raises(Exception) as unavailable:
            await router_module.generate_document_insight(
                "doc-no-file", InsightGenerateRequest(force=False),
                user_id=OWNER, db=session)
        assert getattr(unavailable.value, "status_code", None) == 404


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_reclaim_stale_insight_jobs(factory):
    from app.tasks.insight_task import _reclaim_stale_jobs

    async with factory() as session:
        repo = KnowledgeInsightRepository(session)
        version = await repo.upsert_version(
            owner_user_id=OWNER, space_id=None, source_type="document",
            source_id="doc-timeout", generation=1, content_hash="e" * 32,
            status=SourceVersionStatus.ACTIVE, chunk_count=1)
        job = await repo.upsert_job(
            owner_user_id=OWNER, version=version, artifact_type="summary",
            prompt_version="insight-v1", model_id=None)
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        await session.commit()
        job_id = job.id

    reclaimed = await _reclaim_stale_jobs(10)
    assert reclaimed >= 1
    async with factory() as session:
        job = await session.get(KnowledgeGenerationJob, job_id)
        assert job.status == JobStatus.FAILED
        assert job.error == "execution_timeout"


@pytest.mark.asyncio
async def test_mindmap_edits_persist_and_conflict(client, factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    first = await client.get(
        f"/api/v1/insights/documents/{DOC_ID}/mindmap", headers=_auth(OWNER))
    data = first.json()["data"]
    base_revision = data["revision"]
    assert base_revision and base_revision != "base"
    node_id = data["mindmap"]["root"]["children"][0]["id"]
    edited = await client.patch(
        f"/api/v1/insights/sources/document/{DOC_ID}/mindmap",
        json={"expected_revision": base_revision,
              "edits": [{"node_id": node_id, "label": "用户标题", "sort_order": 0}]},
        headers=_auth(OWNER))
    assert edited.status_code == 200
    new_revision = edited.json()["data"]["revision"]
    assert new_revision != base_revision

    refreshed = await client.get(
        f"/api/v1/insights/documents/{DOC_ID}/mindmap", headers=_auth(OWNER))
    refreshed_data = refreshed.json()["data"]
    assert refreshed_data["revision"] == new_revision
    assert refreshed_data["user_edited_count"] == 1
    assert refreshed_data["mindmap"]["root"]["children"][0]["label"] == "用户标题"
    assert refreshed_data["mindmap"]["root"]["children"][0]["user_edited"] is True

    stale = await client.patch(
        f"/api/v1/insights/sources/document/{DOC_ID}/mindmap",
        json={"expected_revision": base_revision,
              "edits": [{"node_id": node_id, "label": "冲突"}]},
        headers=_auth(OWNER))
    assert stale.status_code == 409

    unknown = await client.patch(
        f"/api/v1/insights/sources/document/{DOC_ID}/mindmap",
        json={"expected_revision": new_revision,
              "edits": [{"node_id": "missing-node", "label": "X"}]},
        headers=_auth(OWNER))
    assert unknown.status_code == 400

    forbidden = await client.patch(
        f"/api/v1/insights/sources/document/{DOC_ID}/mindmap",
        json={"edits": [{"node_id": node_id, "label": "越权"}]},
        headers=_auth(OTHER))
    assert forbidden.status_code == 404

    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document",
            source_id=DOC_ID, force=True)
        await session.commit()
        regen_job_id = job.id
    await claim_and_run_job(regen_job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    after_regen = await client.get(
        f"/api/v1/insights/documents/{DOC_ID}/mindmap", headers=_auth(OWNER))
    after_data = after_regen.json()["data"]
    assert after_data["mindmap"]["root"]["children"][0]["label"] == "用户标题"
    assert after_data["revision"] == new_revision


# ---------------------------------------------------------------------------
# V2: 长文分层分析 / 分析来源 API
# ---------------------------------------------------------------------------

def test_build_analysis_blocks_splits_long_content():
    from app.services.insight_generation_service import _build_analysis_blocks
    anchors = [
        SimpleNamespace(id=f"a{i}", chunk_index=i,
                        heading_path=f"第{i}章",
                        page_no=None,
                        quote_text="内容" * 2000)
        for i in range(4)
    ]
    blocks = _build_analysis_blocks(anchors)
    assert len(blocks) > 1
    assert sum(len(block["anchors"]) for block in blocks) == 4
    assert all(block["label"] for block in blocks)


def _layered_fake_llm(prompt, model_id=None, chat_model=None):
    import re
    ids = re.findall(r"\[([0-9a-f-]{36})\]", prompt)
    first = ids[0] if ids else "missing"
    if "当前片段" in prompt:
        return json.dumps({
            "sections": [{
                "title": "片段",
                "summary": "片段摘要",
                "anchor_ids": [first],
                "claims": [{"text": "片段结论", "anchor_ids": [first]}],
            }],
            "terms": [{"name": "片段概念", "anchor_ids": [first]}],
        }, ensure_ascii=False)
    return json.dumps({
        "overview": {"text": "全文概览", "anchor_ids": [first]},
        "key_points": [{"text": "汇总结论", "anchor_ids": [first]}],
        "sections": [],
        "terms": [{"name": "汇总概念", "anchor_ids": [first]}],
        "open_questions": [],
        "relations": [],
    }, ensure_ascii=False)


@pytest.mark.asyncio
async def test_layered_generation_reports_real_coverage(factory, monkeypatch):
    from app.models.document_index import DocumentIndexStatus
    from app.models.knowledge_insight import KnowledgeArtifact
    from app.services.source_anchor_service import (
        activate_document_version, record_document_anchors,
    )

    doc_id = "doc-long-0000-0000-000000000001"
    sections = []
    source_text = ""
    for index in range(3):
        heading = f"# 第{index + 1}章"
        body = ("这是长文内容。" * 700)
        source_text += f"{heading}\n\n{body}\n\n"
        sections.append(SimpleNamespace(
            page_content=f"{heading}\n\n{body}", metadata={"page": 0}))
    source = [SimpleNamespace(page_content=source_text, metadata={"page": 0})]

    async with factory() as session:
        session.add(DocumentIndex(
            id=doc_id, user_id=OWNER, filename="long.md",
            original_filename="长文档.md", file_path="", md5="9" * 32,
            status=DocumentIndexStatus.INDEXED,
            generation=1, active_generation=1, chunk_count=3,
        ))
        await session.commit()
        await record_document_anchors(
            session, owner_user_id=OWNER, space_id=None,
            document_id=doc_id, generation=1, content_hash="9" * 32,
            source_documents=source, split_documents=sections)
        await activate_document_version(
            session, owner_user_id=OWNER, document_id=doc_id, generation=1)
        await session.commit()
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document",
            source_id=doc_id)
        await session.commit()
        job_id = job.id

    import app.services.insight_generation_service as insight_module
    async def _fake_invoke(prompt, model_id=None, chat_model=None):
        return _layered_fake_llm(prompt, model_id, chat_model)
    monkeypatch.setattr(insight_module, "_invoke_llm", _fake_invoke)
    await claim_and_run_job(job_id, chat_model=_FakeChatModel({}))

    async with factory() as session:
        artifact = (await session.execute(
            select(KnowledgeArtifact).where(
                KnowledgeArtifact.source_id == doc_id,
                KnowledgeArtifact.artifact_type == "summary")
        )).scalar_one()
        payload = artifact.payload_json
        coverage = payload["meta"]["coverage"]
        assert coverage["total_blocks"] > 1
        assert coverage["analyzed_blocks"] > 1
        assert coverage["partial"] is False
        assert payload["overview"]["text"] == "全文概览"
        assert payload["key_points"]


@pytest.mark.asyncio
async def test_analysis_source_endpoints(client, factory, seeded):
    version_id, anchor_ids = seeded
    async with factory() as session:
        job, _, _ = await queue_summary_generation(
            session, owner_user_id=OWNER, source_type="document", source_id=DOC_ID)
        await session.commit()
        job_id = job.id
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(_llm_payload(anchor_ids)))

    sources = await client.get(
        "/api/v1/insights/sources", params={"source_type": "document"},
        headers=_auth(OWNER))
    assert sources.status_code == 200
    assert sources.json()["data"]["sources"][0]["source_id"] == DOC_ID

    recent = await client.get("/api/v1/insights/recent", headers=_auth(OWNER))
    assert recent.status_code == 200
    assert recent.json()["data"]["items"][0]["status"] == "succeeded"

    state = await client.get(
        f"/api/v1/insights/sources/document/{DOC_ID}/state",
        headers=_auth(OWNER))
    assert state.status_code == 200
    assert state.json()["data"]["status"] == "ready"

    text = await client.get(
        f"/api/v1/insights/sources/document/{DOC_ID}/text",
        headers=_auth(OWNER))
    assert text.status_code == 200
    assert len(text.json()["data"]["segments"]) == 2

    forbidden = await client.get(
        f"/api/v1/insights/sources/document/{DOC_ID}/text",
        headers=_auth(OTHER))
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_protected_document_file_endpoint(factory, tmp_path):
    from fastapi import HTTPException
    from app.models.document_index import DocumentIndexStatus
    from app.router.knowledge_router import get_document_file

    file_path = tmp_path / "source.md"
    file_path.write_text("# 标题\n\n正文", encoding="utf-8")
    async with factory() as session:
        session.add(DocumentIndex(
            id="doc-file", user_id=OWNER, filename="source.md",
            original_filename="source.md", file_path=str(file_path),
            md5="8" * 32, status=DocumentIndexStatus.INDEXED,
            generation=1, active_generation=1, chunk_count=1,
        ))
        await session.commit()
        response = await get_document_file("doc-file", user_id=OWNER, db=session)
        assert str(response.path) == str(file_path)
        with pytest.raises(HTTPException) as forbidden:
            await get_document_file("doc-file", user_id=OTHER, db=session)
        assert forbidden.value.status_code == 404
