"""知识洞察与知识图谱 API（N1-N3）。

所有入口先按 owner 定位资源；证据读取额外复核来源版本 active 与文档
active_generation。资源不存在与无权限统一返回 404，避免泄漏存在性。
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger_handler import logger
from app.core.permission import ensure_space_member
from app.core.success_response import success_response
from app.db.db_config import get_db
from app.models.document_index import DocumentIndex
from app.models.knowledge_insight import (
    EvidenceStatus, JobStatus, KnowledgeArtifact, KnowledgeConcept, KnowledgeGenerationJob,
    KnowledgeRelation, KnowledgeSourceVersion, SourceType, SourceVersionStatus,
)
from app.models.note import Note
from app.repositories.knowledge_insight_repository import KnowledgeInsightRepository
from app.schemas.insight_models import (
    ConceptPatch, InsightGenerateRequest, InsightGenerateResponse,
    MindmapEditRequest, RelationPatch,
)
from app.services.insight_generation_service import (
    MindmapConflict, MindmapNotFound, SourceUnavailable, analysis_result_status,
    dispatch_insight_job, ensure_source_version, get_active_summary,
    get_mindmap_with_edits, queue_summary_generation, save_mindmap_edits,
)
from app.services.source_anchor_service import verify_anchor
from app.utils.auth_utils import get_current_user_id

insight_router = APIRouter(prefix="/insights", tags=["insights"])


async def _owned_document(db: AsyncSession, document_id: str, user_id: str) -> DocumentIndex:
    document = await db.get(DocumentIndex, document_id)
    if document is None or document.user_id != user_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


async def _owned_note(db: AsyncSession, note_id: str, user_id: str) -> Note:
    note = await db.get(Note, note_id)
    if note is None or note.user_id != user_id:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return note


async def _fail_dispatch(job_id: str, error: Exception) -> None:
    from app.db.db_config import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(KnowledgeGenerationJob, job_id)
            if job is not None and job.status == JobStatus.QUEUED:
                job.status = JobStatus.FAILED
                job.error = f"dispatch_failed: {str(error)[:200]}"
                await session.commit()
    except Exception as exc:
        logger.error("【知识洞察】队列失败状态写入失败: %s", str(exc)[:200])


async def _start_generation(    db: AsyncSession,
    *,
    user_id: str,
    source_type: str,
    source_id: str,
    payload: InsightGenerateRequest,
) -> InsightGenerateResponse:
    try:
        job, already_generated, artifact_id = await queue_summary_generation(
            db,
            owner_user_id=user_id,
            source_type=source_type,
            source_id=source_id,
            force=payload.force,
            model_id=payload.model,
        )
    except SourceUnavailable as exc:
        reason = str(exc)
        if reason == "forbidden":
            raise HTTPException(status_code=404, detail="资源不存在")
        raise HTTPException(status_code=409, detail=f"来源不可用: {reason}")

    await db.commit()

    if not already_generated and job.status == JobStatus.QUEUED:
        try:
            dispatch_insight_job(job.id)
        except Exception as exc:
            logger.error("【知识洞察】任务投递失败: job=%s, error=%s", job.id, str(exc)[:200])
            await _fail_dispatch(job.id, exc)
            raise HTTPException(status_code=503, detail="任务队列不可用，请稍后重试")

    return InsightGenerateResponse(
        job_id=job.id,
        status=job.status,
        already_generated=already_generated,
        artifact_id=artifact_id,
        message=("已存在可信摘要" if already_generated else "任务已受理"),
    )


def _job_payload(job: KnowledgeGenerationJob) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "source_type": job.source_type,
        "source_id": job.source_id,
        "source_version_id": job.source_version_id,
        "artifact_type": job.artifact_type,
        "attempts": job.attempts,
        "model_id": job.model_id,
        "error": job.error,
        "started_at": str(job.started_at) if job.started_at else None,
        "finished_at": str(job.finished_at) if job.finished_at else None,
        "created_at": str(job.created_at) if job.created_at else None,
    }


async def _source_identity(db: AsyncSession, source_type: str, source_id: str,
                           user_id: str) -> tuple[str, Optional[str], bool]:
    """返回 (标题, 文件URL或None, 文件是否可用)，同时完成 owner 校验。"""
    if source_type == SourceType.DOCUMENT:
        document = await _owned_document(db, source_id, user_id)
        file_ok = bool(document.file_path and os.path.isfile(document.file_path))
        return (document.original_filename,
                f"/api/v1/knowledge/documents/{document.id}/file" if file_ok else None,
                file_ok)
    if source_type == SourceType.NOTE:
        note = await _owned_note(db, source_id, user_id)
        return note.title, None, False
    raise HTTPException(status_code=400, detail="未知来源类型")


@insight_router.get("/sources")
async def list_analysis_sources(
    source_type: Optional[str] = Query(None, pattern="^(note|document)$"),
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    results: list[dict] = []
    if source_type in (None, SourceType.NOTE):
        stmt = select(
            Note.id, Note.title, Note.category, Note.updated_at
        ).where(Note.user_id == user_id)
        if q:
            stmt = stmt.where(Note.title.ilike(f"%{q}%"))
        for row in (await db.execute(
                stmt.order_by(Note.updated_at.desc()).limit(limit))).all():
            results.append({
                "source_type": "note",
                "source_id": row.id,
                "title": row.title,
                "category": row.category,
                "resource_type": "笔记",
                "updated_at": str(row.updated_at) if row.updated_at else None,
            })
    if source_type in (None, SourceType.DOCUMENT):
        stmt = select(
            DocumentIndex.id, DocumentIndex.original_filename,
            DocumentIndex.file_type, DocumentIndex.status,
            DocumentIndex.updated_at,
        ).where(DocumentIndex.user_id == user_id)
        if q:
            stmt = stmt.where(DocumentIndex.original_filename.ilike(f"%{q}%"))
        for row in (await db.execute(
                stmt.order_by(DocumentIndex.updated_at.desc()).limit(limit))).all():
            results.append({
                "source_type": "document",
                "source_id": row.id,
                "title": row.original_filename,
                "file_type": row.file_type,
                "index_status": row.status.value if hasattr(row.status, "value") else row.status,
                "resource_type": "知识库文档",
                "updated_at": str(row.updated_at) if row.updated_at else None,
            })
    results.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return success_response(data={
        "sources": results[:limit],
        "total": len(results),
    })


@insight_router.get("/recent")
async def recent_analyses(
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    rows = list((await db.execute(
        select(KnowledgeGenerationJob)
        .where(KnowledgeGenerationJob.owner_user_id == user_id,
               KnowledgeGenerationJob.artifact_type == "summary")
        .order_by(KnowledgeGenerationJob.updated_at.desc())
        .limit(limit)
    )).scalars().all())
    note_ids = [job.source_id for job in rows if job.source_type == "note"]
    doc_ids = [job.source_id for job in rows if job.source_type == "document"]
    version_ids = [job.source_version_id for job in rows]
    partial_versions = set()
    if version_ids:
        artifacts = (await db.execute(select(KnowledgeArtifact).where(
            KnowledgeArtifact.owner_user_id == user_id,
            KnowledgeArtifact.artifact_type == "summary",
            KnowledgeArtifact.status == "active",
            KnowledgeArtifact.source_version_id.in_(version_ids),
        ))).scalars().all()
        partial_versions = {item.source_version_id for item in artifacts
                            if analysis_result_status(item.payload_json or {}) == "partial"}
    titles: dict[str, str] = {}
    if note_ids:
        for note_id, title in (await db.execute(
                select(Note.id, Note.title).where(
                    Note.user_id == user_id, Note.id.in_(note_ids)))).all():
            titles[note_id] = title
    if doc_ids:
        for doc_id, filename in (await db.execute(
                select(DocumentIndex.id, DocumentIndex.original_filename).where(
                    DocumentIndex.user_id == user_id,
                    DocumentIndex.id.in_(doc_ids)))).all():
            titles[doc_id] = filename
    return success_response(data={
        "items": [{
            **_job_payload(job),
            "status": ("partial" if job.status == "succeeded"
                       and job.source_version_id in partial_versions else job.status),
            "title": titles.get(job.source_id, job.source_id),
            "resource_type": "笔记" if job.source_type == "note" else "知识库文档",
        } for job in rows],
    })


@insight_router.get("/sources/{source_type}/{source_id}/state")
async def analysis_source_state(
    source_type: str,
    source_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    title, _, _ = await _source_identity(db, source_type, source_id, user_id)
    repo = KnowledgeInsightRepository(db)
    version = await repo.get_active_version(
        owner_user_id=user_id, source_type=source_type, source_id=source_id)
    latest_version = version or await repo.get_latest_version(
        owner_user_id=user_id, source_type=source_type, source_id=source_id)
    artifact = await repo.get_artifact(
        owner_user_id=user_id, source_type=source_type, source_id=source_id,
        artifact_type="summary", active_only=False)
    job = (await repo.get_job_for_version(latest_version.id, "summary")
           if latest_version is not None else None)

    result_available = bool(artifact is not None and artifact.status == "active"
            and version is not None
            and artifact.source_version_id == version.id)
    result_status = (analysis_result_status(artifact.payload_json or {})
                     if result_available else None)
    status = "not_generated"
    if job is not None and job.status in ("queued", "running", "failed"):
        status = job.status
    elif result_available:
        status = result_status
    elif artifact is not None:
        status = "stale"
    elif job is not None and job.status in ("failed", "stale"):
        status = job.status

    return success_response(data={
        "source_type": source_type,
        "source_id": source_id,
        "title": title,
        "source_version_id": version.id if version else None,
        "generation": version.generation if version else None,
        "status": status,
        "result_available": result_available,
        "result_status": result_status,
        "coverage": ((artifact.payload_json or {}).get("meta") or {}).get("coverage")
        if result_available else None,
        "artifact_id": artifact.id if artifact else None,
        "artifact_status": artifact.status if artifact else None,
        "artifact_updated_at": str(artifact.updated_at) if artifact and artifact.updated_at else None,
        "job": _job_payload(job) if job else None,
    })


@insight_router.get("/sources/{source_type}/{source_id}/text")
async def analysis_source_text(
    source_type: str,
    source_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    title, file_url, file_ok = await _source_identity(
        db, source_type, source_id, user_id)
    try:
        version = await ensure_source_version(
            db, owner_user_id=user_id, source_type=source_type,
            source_id=source_id)
        await db.commit()
    except SourceUnavailable as exc:
        reason = str(exc)
        if reason == "forbidden":
            raise HTTPException(status_code=404, detail="资源不存在")
        raise HTTPException(status_code=409, detail=f"原文不可用: {reason}")

    repo = KnowledgeInsightRepository(db)
    anchors = await repo.list_anchors(version.id)
    truncated = len(anchors) > 500
    segments = [{
        "anchor_id": anchor.id,
        "page_no": anchor.page_no,
        "heading_path": anchor.heading_path,
        "char_start": anchor.char_start,
        "char_end": anchor.char_end,
        "locator_kind": anchor.locator_kind,
        "text": anchor.quote_text,
    } for anchor in anchors[:500]]
    return success_response(data={
        "source_type": source_type,
        "source_id": source_id,
        "title": title,
        "source_version_id": version.id,
        "generation": version.generation,
        "file_url": file_url,
        "file_available": file_ok,
        "segments": segments,
        "truncated": truncated,
    })


async def _serialize_evidence(db: AsyncSession, artifact_id: str) -> dict:
    repo = KnowledgeInsightRepository(db)
    rows = await repo.list_evidence(artifact_id)
    anchors = await repo.get_anchors_by_ids(
        [row.anchor_id for row in rows])
    anchor_map = {anchor.id: anchor for anchor in anchors}
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        anchor = anchor_map.get(row.anchor_id)
        if anchor is None or row.verification_status != EvidenceStatus.VERIFIED:
            continue
        grouped.setdefault(row.target_id, []).append({
            "anchor_id": anchor.id,
            "quote": anchor.quote_text,
            "page_no": anchor.page_no,
            "heading_path": anchor.heading_path,
            "char_start": anchor.char_start,
            "char_end": anchor.char_end,
            "locator_kind": anchor.locator_kind,
            "confidence": anchor.extraction_confidence,
            "supporting_quote": row.supporting_quote,
            "verification_status": row.verification_status,
        })
    return grouped


async def _insight_response(db: AsyncSession, *, user_id: str, source_type: str,
                            source_id: str, title: str) -> dict:
    payload, artifact = await get_active_summary(
        db, owner_user_id=user_id, source_type=source_type, source_id=source_id)
    if artifact is None:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "status": "not_generated",
            "artifact": None,
            "evidence": {},
        }
    if payload is None:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "status": "stale",
            "artifact": None,
            "evidence": {},
        }
    evidence = await _serialize_evidence(db, artifact.id)
    return {
        "source_type": source_type,
        "source_id": source_id,
        "title": title,
        "status": "ready",
        "source_version_id": artifact.source_version_id,
        "generation": artifact.generation,
        "artifact_id": artifact.id,
        "artifact": payload,
        "evidence": evidence,
    }


@insight_router.post("/documents/{document_id}/generate")
async def generate_document_insight(
    document_id: str,
    payload: InsightGenerateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    document = await _owned_document(db, document_id, user_id)
    result = await _start_generation(
        db, user_id=user_id, source_type=SourceType.DOCUMENT,
        source_id=document.id, payload=payload)
    return success_response(data=result.model_dump())


@insight_router.post("/notes/{note_id}/generate")
async def generate_note_insight(
    note_id: str,
    payload: InsightGenerateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    note = await _owned_note(db, note_id, user_id)
    result = await _start_generation(
        db, user_id=user_id, source_type=SourceType.NOTE,
        source_id=note.id, payload=payload)
    return success_response(data=result.model_dump())


@insight_router.get("/jobs/{job_id}")
async def get_insight_job(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(KnowledgeGenerationJob, job_id)
    if job is None or job.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success_response(data=_job_payload(job))


@insight_router.get("/documents/{document_id}")
async def get_document_insight(
    document_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    document = await _owned_document(db, document_id, user_id)
    data = await _insight_response(
        db, user_id=user_id, source_type=SourceType.DOCUMENT,
        source_id=document.id, title=document.original_filename)
    return success_response(data=data)


@insight_router.get("/documents/{document_id}/mindmap")
async def get_document_mindmap(
    document_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    document = await _owned_document(db, document_id, user_id)
    payload, artifact, revision, edit_count = await get_mindmap_with_edits(
        db, owner_user_id=user_id, source_type=SourceType.DOCUMENT,
        source_id=document.id)
    if artifact is None:
        return success_response(data={"status": "not_generated", "mindmap": None})
    if payload is None:
        return success_response(data={"status": "stale", "mindmap": None})
    return success_response(data={
        "status": "ready",
        "source_version_id": artifact.source_version_id,
        "generation": artifact.generation,
        "revision": revision,
        "user_edited_count": edit_count,
        "mindmap": payload,
    })


@insight_router.get("/notes/{note_id}")
async def get_note_insight(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    note = await _owned_note(db, note_id, user_id)
    data = await _insight_response(
        db, user_id=user_id, source_type=SourceType.NOTE,
        source_id=note.id, title=note.title)
    return success_response(data=data)


@insight_router.get("/notes/{note_id}/mindmap")
async def get_note_mindmap(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    note = await _owned_note(db, note_id, user_id)
    payload, artifact, revision, edit_count = await get_mindmap_with_edits(
        db, owner_user_id=user_id, source_type=SourceType.NOTE,
        source_id=note.id)
    if artifact is None:
        return success_response(data={"status": "not_generated", "mindmap": None})
    if payload is None:
        return success_response(data={"status": "stale", "mindmap": None})
    return success_response(data={
        "status": "ready",
        "source_version_id": artifact.source_version_id,
        "generation": artifact.generation,
        "revision": revision,
        "user_edited_count": edit_count,
        "mindmap": payload,
    })


@insight_router.patch("/sources/{source_type}/{source_id}/mindmap")
async def patch_mindmap(
    source_type: str,
    source_id: str,
    payload: MindmapEditRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if source_type == SourceType.DOCUMENT:
        await _owned_document(db, source_id, user_id)
    elif source_type == SourceType.NOTE:
        await _owned_note(db, source_id, user_id)
    else:
        raise HTTPException(status_code=400, detail="未知来源类型")

    try:
        mindmap, revision, edit_count = await save_mindmap_edits(
            db, owner_user_id=user_id, source_type=source_type,
            source_id=source_id, expected_revision=payload.expected_revision,
            edits=payload.edits)
    except MindmapConflict:
        raise HTTPException(status_code=409, detail="导图已被其他修改更新，请刷新后重试")
    except MindmapNotFound:
        raise HTTPException(status_code=409, detail="当前没有可编辑的导图")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return success_response(data={
        "mindmap": mindmap,
        "revision": revision,
        "user_edited_count": edit_count,
    })


@insight_router.get("/anchors/{anchor_id}")
async def get_anchor(
    anchor_id: str,
    quote: Optional[str] = Query(None, description="待核验引文（可选）"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    verification = await verify_anchor(
        db, owner_user_id=user_id, anchor_id=anchor_id, quote=quote)
    if verification.anchor is None:
        if verification.reason == "forbidden":
            raise HTTPException(status_code=403, detail="无权访问该证据")
        raise HTTPException(status_code=404, detail="证据不存在")
    if not verification.verified:
        status = 409 if verification.reason in (
            "stale_version", "generation_superseded") else 422
        raise HTTPException(status_code=status, detail=verification.reason)
    anchor = verification.anchor
    return success_response(data={
        "anchor_id": anchor.id,
        "source_type": anchor.source_type,
        "source_id": anchor.source_id,
        "generation": anchor.generation,
        "chunk_id": anchor.chunk_id,
        "chunk_index": anchor.chunk_index,
        "page_no": anchor.page_no,
        "heading_path": anchor.heading_path,
        "char_start": anchor.char_start,
        "char_end": anchor.char_end,
        "quote": anchor.quote_text,
        "quote_hash": anchor.quote_hash,
        "locator_kind": anchor.locator_kind,
        "extraction_confidence": anchor.extraction_confidence,
        "verified": True,
    })


# ---------------------------------------------------------------------------
# 知识图谱
# ---------------------------------------------------------------------------

async def _active_version_ids(db: AsyncSession, version_ids: list[str]) -> set[str]:
    if not version_ids:
        return set()
    result = await db.execute(
        select(KnowledgeSourceVersion.id).where(
            KnowledgeSourceVersion.id.in_(version_ids),
            KnowledgeSourceVersion.status == SourceVersionStatus.ACTIVE,
        )
    )
    return set(result.scalars().all())


async def _active_evidence(rows: list, db: AsyncSession) -> list:
    active_ids = await _active_version_ids(
        db, list({row.source_version_id for row in rows}))
    return [row for row in rows if row.source_version_id in active_ids]


@insight_router.get("/graph")
async def get_graph(
    space_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="概念名筛选"),
    limit: int = Query(200, ge=1, le=500),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if space_id:
        await ensure_space_member(space_id, user_id, db)
    space_key = space_id or ""
    repo = KnowledgeInsightRepository(db)
    concepts = await repo.list_concepts(
        owner_user_id=user_id, space_key=space_key, query=q, limit=limit)
    relations = await repo.list_relations(
        owner_user_id=user_id, space_key=space_key, limit=min(limit * 2, 1000))

    relation_evidence = await _active_evidence(
        await repo.list_active_evidence_for_relations(
            [relation.id for relation in relations]), db)
    valid_relation_ids = {row.target_id for row in relation_evidence}
    relations = [r for r in relations if r.id in valid_relation_ids]

    concept_evidence = await _active_evidence(
        await repo.list_active_evidence_for_concepts(
            [c.id for c in concepts]), db)
    evidenced_concepts = {row.target_id for row in concept_evidence}
    endpoint_ids: set[str] = set()
    for relation in relations:
        endpoint_ids.add(relation.source_concept_id)
        endpoint_ids.add(relation.target_concept_id)
    visible_concept_ids = evidenced_concepts | endpoint_ids

    concept_by_id = {concept.id: concept for concept in concepts}
    missing_ids = [cid for cid in visible_concept_ids if cid not in concept_by_id]
    if missing_ids:
        result = await db.execute(
            select(KnowledgeConcept).where(
                KnowledgeConcept.owner_user_id == user_id,
                KnowledgeConcept.space_key == space_key,
                KnowledgeConcept.id.in_(missing_ids),
            ))
        for concept in result.scalars().all():
            concept_by_id[concept.id] = concept

    # 用户拒绝的概念不进入图谱；连接被拒绝概念的关系也隐藏
    relations = [
        relation for relation in relations
        if relation.source_concept_id in concept_by_id
        and relation.target_concept_id in concept_by_id
        and concept_by_id[relation.source_concept_id].status != "rejected"
        and concept_by_id[relation.target_concept_id].status != "rejected"
    ]
    visible_concept_ids = {
        concept_id for concept_id in visible_concept_ids
        if concept_id in concept_by_id
        and concept_by_id[concept_id].status != "rejected"
    }

    relation_docs: dict[str, set[str]] = {}
    relation_counts: dict[str, int] = {}
    for row in relation_evidence:
        relation_docs.setdefault(row.target_id, set()).add(row.source_id)
        relation_counts[row.target_id] = relation_counts.get(row.target_id, 0) + 1
    concept_docs: dict[str, set[str]] = {}
    concept_counts: dict[str, int] = {}
    for row in concept_evidence:
        concept_docs.setdefault(row.target_id, set()).add(row.source_id)
        concept_counts[row.target_id] = concept_counts.get(row.target_id, 0) + 1

    nodes = []
    for concept_id, concept in concept_by_id.items():
        if concept_id not in visible_concept_ids:
            continue
        nodes.append({
            "id": concept.id,
            "name": concept.canonical_name,
            "description": concept.description,
            "aliases": concept.aliases_json or [],
            "status": concept.status,
            "evidence_count": concept_counts.get(concept.id, 0),
            "document_ids": sorted(concept_docs.get(concept.id, set())),
        })
    edges = []
    for relation in relations:
        edges.append({
            "id": relation.id,
            "source": relation.source_concept_id,
            "target": relation.target_concept_id,
            "type": relation.relation_type,
            "description": relation.description,
            "verification_state": relation.verification_state,
            "confidence": relation.confidence,
            "evidence_count": relation_counts.get(relation.id, 0),
            "document_ids": sorted(relation_docs.get(relation.id, set())),
        })
    return success_response(data={
        "space_id": space_id,
        "nodes": nodes,
        "edges": edges,
        "limits": {"concepts": limit, "relations": min(limit * 2, 1000)},
    })


async def _relation_evidence_payload(db: AsyncSession, relation_id: str,
                                     user_id: str) -> list[dict]:
    repo = KnowledgeInsightRepository(db)
    rows = await _active_evidence(
        await repo.list_evidence_for_target(
            owner_user_id=user_id, target_kind="relation", target_id=relation_id),
        db)
    anchors = await repo.get_anchors_by_ids([row.anchor_id for row in rows])
    anchor_map = {anchor.id: anchor for anchor in anchors}
    payload = []
    for row in rows:
        anchor = anchor_map.get(row.anchor_id)
        if anchor is None:
            continue
        payload.append({
            "anchor_id": anchor.id,
            "source_id": row.source_id,
            "source_version_id": row.source_version_id,
            "quote": anchor.quote_text,
            "page_no": anchor.page_no,
            "heading_path": anchor.heading_path,
        })
    return payload


@insight_router.get("/graph/nodes/{concept_id}")
async def get_graph_node(
    concept_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    concept = await db.get(KnowledgeConcept, concept_id)
    if concept is None or concept.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="概念不存在")
    repo = KnowledgeInsightRepository(db)
    evidence = await _active_evidence(
        await repo.list_evidence_for_target(
            owner_user_id=user_id, target_kind="concept", target_id=concept.id),
        db)
    anchors = await repo.get_anchors_by_ids([row.anchor_id for row in evidence])
    anchor_map = {anchor.id: anchor for anchor in anchors}
    evidence_payload = [{
        "anchor_id": row.anchor_id,
        "source_id": row.source_id,
        "quote": anchor_map[row.anchor_id].quote_text,
        "page_no": anchor_map[row.anchor_id].page_no,
        "heading_path": anchor_map[row.anchor_id].heading_path,
    } for row in evidence if row.anchor_id in anchor_map]
    return success_response(data={
        "id": concept.id,
        "name": concept.canonical_name,
        "description": concept.description,
        "aliases": concept.aliases_json or [],
        "status": concept.status,
        "evidence": evidence_payload,
    })


@insight_router.get("/graph/relations/{relation_id}")
async def get_graph_relation(
    relation_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    relation = await db.get(KnowledgeRelation, relation_id)
    if relation is None or relation.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="关系不存在")
    return success_response(data={
        "id": relation.id,
        "source": relation.source_concept_id,
        "target": relation.target_concept_id,
        "type": relation.relation_type,
        "description": relation.description,
        "verification_state": relation.verification_state,
        "evidence": await _relation_evidence_payload(db, relation.id, user_id),
    })


@insight_router.patch("/graph/nodes/{concept_id}")
async def patch_graph_node(
    concept_id: str,
    payload: ConceptPatch,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    concept = await db.get(KnowledgeConcept, concept_id)
    if concept is None or concept.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="概念不存在")
    concept.status = payload.status
    await db.commit()
    return success_response(data={"id": concept.id, "status": concept.status})


@insight_router.patch("/graph/relations/{relation_id}")
async def patch_graph_relation(
    relation_id: str,
    payload: RelationPatch,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    relation = await db.get(KnowledgeRelation, relation_id)
    if relation is None or relation.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="关系不存在")
    relation.verification_state = payload.verification_state
    await db.commit()
    return success_response(data={"id": relation.id,
                                  "verification_state": relation.verification_state})
