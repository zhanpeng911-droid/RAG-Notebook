"""T4: real upload -> parser/splitter -> SQL -> persistent Chroma -> analysis."""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
from jose import jwt
from sqlalchemy import select

from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.models.knowledge_insight import (
    KnowledgeArtifact, KnowledgeGenerationJob, SourceAnchor,
)
from tools.backend_acceptance import dataset

API = os.environ.get("NB_ACCEPTANCE_API_BASE", "http://backend:8000/api/v1")
SECRET = os.environ["SECRET_KEY"]


def _auth(owner: str) -> dict:
    token = jwt.encode({"user_id": owner, "user_name": "acceptance"},
                       SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _dataset_root() -> Path:
    base = Path(os.environ.get("NB_ACCEPTANCE_DATA_DIR", "/app/data"))
    root = base / "acceptance-dataset" / os.environ["NB_ACCEPTANCE_RUN_ID"]
    if not root.exists():
        dataset.generate(root)
    return root


async def _upload(client: httpx.AsyncClient, owner: str, path: Path):
    with path.open("rb") as stream:
        return await client.post(
            f"{API}/knowledge/add/single",
            headers=_auth(owner),
            files={"file": (path.name, stream, "text/plain")},
            timeout=900,
        )


async def _wait_document(mysql_sessions, owner: str, filename: str, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with mysql_sessions() as session:
            doc = (await session.execute(select(DocumentIndex).where(
                DocumentIndex.user_id == owner,
                DocumentIndex.original_filename == filename,
            ))).scalar_one_or_none()
            if doc is not None and doc.status in (
                    DocumentIndexStatus.INDEXED, DocumentIndexStatus.INDEX_FAILED):
                return doc
        await asyncio.sleep(1)
    raise AssertionError(f"document {filename} did not reach a terminal state")


@pytest.mark.asyncio
async def test_medium_text_upload_index_and_layered_analysis(
        mysql_sessions, request):
    root = _dataset_root()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["name"] == "medium.txt")
    owner = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        started = time.monotonic()
        response = await _upload(client, owner, root / "medium.txt")
        upload_seconds = time.monotonic() - started
        assert response.status_code == 200, response.text
        doc = await _wait_document(mysql_sessions, owner, "medium.txt")
        assert doc.status == DocumentIndexStatus.INDEXED, doc.error_message
        assert doc.md5 == entry["sha256"][:32] or len(doc.md5) == 32
        assert doc.active_generation >= 1

        async with mysql_sessions() as session:
            anchors = list((await session.execute(select(SourceAnchor).where(
                SourceAnchor.source_version_id == (
                    select(SourceAnchor.source_version_id).where(
                        SourceAnchor.owner_user_id == owner,
                        SourceAnchor.source_id == doc.id,
                    ).limit(1).scalar_subquery()
                ),
            ).order_by(SourceAnchor.chunk_index))).scalars().all())
        assert anchors
        assert all(anchor.page_no is None for anchor in anchors)
        assert [anchor.chunk_index for anchor in anchors] == list(range(len(anchors)))
        source_text = (root / "medium.txt").read_text(encoding="utf-8")
        assert anchors[0].quote_text.strip() in source_text

        generate = await client.post(
            f"{API}/insights/documents/{doc.id}/generate",
            headers=_auth(owner), json={"force": True}, timeout=60)
        assert generate.status_code == 200, generate.text
        job_id = generate.json()["data"]["job_id"]
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = await client.get(f"{API}/insights/jobs/{job_id}",
                                      headers=_auth(owner), timeout=30)
            assert status.status_code == 200
            data = status.json()["data"]
            if data["status"] in ("succeeded", "failed", "stale"):
                break
            await asyncio.sleep(2)
        assert data["status"] == "succeeded", data
        async with mysql_sessions() as session:
            job = await session.get(KnowledgeGenerationJob, job_id)
            artifact = (await session.execute(select(KnowledgeArtifact).where(
                KnowledgeArtifact.source_version_id == job.source_version_id,
                KnowledgeArtifact.artifact_type == "summary",
            ))).scalar_one()
            payload = artifact.payload_json
            coverage = payload.get("meta", {}).get("coverage")
            assert coverage is not None
            assert coverage["total_blocks"] >= coverage["analyzed_blocks"] >= 1
            assert coverage["analyzed_blocks"] <= 8
            assert coverage["missing_count"] == (
                coverage["total_blocks"] - coverage["analyzed_blocks"])
            assert coverage["missing_count"] >= len(coverage["missing_blocks"])
            assert coverage["partial"] is True
            assert payload["trust_status"] in ("verified", "anchor_verified")
        print(json.dumps({
            "case": "T4-medium-text", "upload_seconds": round(upload_seconds, 2),
            "bytes": entry["bytes"], "anchors": len(anchors),
            "coverage": coverage, "trust_status": payload["trust_status"],
            "api_peak_rss": "not_collected_inside_controller",
        }, ensure_ascii=False))


@pytest.mark.asyncio
async def test_small_pdf_anchors_follow_real_pages(mysql_sessions):
    root = _dataset_root()
    owner = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        response = await _upload(client, owner, root / "small.pdf")
        assert response.status_code == 200, response.text
        doc = await _wait_document(mysql_sessions, owner, "small.pdf")
        assert doc.status == DocumentIndexStatus.INDEXED, doc.error_message
        async with mysql_sessions() as session:
            version_id = (await session.execute(select(SourceAnchor.source_version_id).where(
                SourceAnchor.owner_user_id == owner,
                SourceAnchor.source_id == doc.id,
            ).limit(1))).scalar_one()
            anchors = list((await session.execute(select(SourceAnchor).where(
                SourceAnchor.source_version_id == version_id,
            ).order_by(SourceAnchor.chunk_index))).scalars().all())
        pages = {anchor.page_no for anchor in anchors if anchor.page_no is not None}
        assert pages
        assert pages == set(range(1, 9))


@pytest.mark.asyncio
async def test_note_reads_stay_responsive_during_large_upload(mysql_sessions):
    root = _dataset_root()
    owner = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        upload = asyncio.create_task(_upload(client, owner, root / "medium.txt"))
        await asyncio.sleep(0.2)
        latencies = []
        for _ in range(10):
            start = time.monotonic()
            response = await client.get(f"{API}/note/list", headers=_auth(owner),
                                        timeout=15)
            latencies.append(time.monotonic() - start)
            assert response.status_code == 200
            await asyncio.sleep(0.1)
        upload_response = await upload
        assert upload_response.status_code == 200, upload_response.text
    assert max(latencies) < 10, max(latencies)
    print(json.dumps({"case": "T4-concurrent-read",
                      "max_latency_seconds": round(max(latencies), 3),
                      "samples": len(latencies)}))
