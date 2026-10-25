"""知识来源版本、锚点、产物、证据与图谱的持久化访问。"""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import delete, select, update

from app.models.knowledge_insight import (
    EvidenceStatus, KnowledgeArtifact, KnowledgeConcept,
    KnowledgeEvidence, KnowledgeGenerationJob, KnowledgeRelation,
    KnowledgeSourceVersion, SourceAnchor, SourceVersionStatus,
)
from app.models.note_graph import KnowledgeMindmapEdit


class KnowledgeInsightRepository:
    """所有方法只 flush，不 commit；事务边界由调用方持有。"""

    def __init__(self, session):
        self.session = session

    # ---------- source version ----------

    async def get_version(self, *, source_type: str, source_id: str,
                          generation: int) -> Optional[KnowledgeSourceVersion]:
        result = await self.session.execute(
            select(KnowledgeSourceVersion).where(
                KnowledgeSourceVersion.source_type == source_type,
                KnowledgeSourceVersion.source_id == source_id,
                KnowledgeSourceVersion.generation == int(generation),
            )
        )
        return result.scalar_one_or_none()

    async def get_version_by_id(self, version_id: str) -> Optional[KnowledgeSourceVersion]:
        return await self.session.get(KnowledgeSourceVersion, version_id)

    async def get_active_version(self, *, owner_user_id: str, source_type: str,
                                 source_id: str) -> Optional[KnowledgeSourceVersion]:
        result = await self.session.execute(
            select(KnowledgeSourceVersion).where(
                KnowledgeSourceVersion.owner_user_id == owner_user_id,
                KnowledgeSourceVersion.source_type == source_type,
                KnowledgeSourceVersion.source_id == source_id,
                KnowledgeSourceVersion.status == SourceVersionStatus.ACTIVE,
            ).order_by(KnowledgeSourceVersion.generation.desc())
        )
        return result.scalars().first()

    async def get_latest_version(self, *, owner_user_id: str, source_type: str,
                                 source_id: str) -> Optional[KnowledgeSourceVersion]:
        result = await self.session.execute(
            select(KnowledgeSourceVersion).where(
                KnowledgeSourceVersion.owner_user_id == owner_user_id,
                KnowledgeSourceVersion.source_type == source_type,
                KnowledgeSourceVersion.source_id == source_id,
            ).order_by(KnowledgeSourceVersion.generation.desc())
        )
        return result.scalars().first()

    async def upsert_version(self, *, owner_user_id: str, space_id: Optional[str],
                             source_type: str, source_id: str, generation: int,
                             content_hash: Optional[str], status: str,
                             chunk_count: int, parser_version: str = "anchor-v1",
                             ) -> KnowledgeSourceVersion:
        version = await self.get_version(
            source_type=source_type, source_id=source_id, generation=generation)
        if version is None:
            version = KnowledgeSourceVersion(
                id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                space_id=space_id or None,
                source_type=source_type,
                source_id=source_id,
                generation=int(generation),
                content_hash=content_hash,
                parser_version=parser_version,
                status=status,
                chunk_count=chunk_count,
            )
            self.session.add(version)
        else:
            version.owner_user_id = owner_user_id
            version.space_id = space_id or None
            version.content_hash = content_hash
            version.parser_version = parser_version
            version.status = status
            version.chunk_count = chunk_count
        await self.session.flush()
        return version

    async def replace_anchors(self, version: KnowledgeSourceVersion,
                              candidates: Sequence) -> list[SourceAnchor]:
        """幂等替换：同一版本重跑不会产生重复锚点。"""
        await self.session.execute(
            delete(SourceAnchor).where(
                SourceAnchor.source_version_id == version.id)
        )
        rows: list[SourceAnchor] = []
        for c in candidates:
            row = SourceAnchor(
                id=str(uuid.uuid4()),
                source_version_id=version.id,
                owner_user_id=version.owner_user_id,
                space_id=version.space_id,
                source_type=version.source_type,
                source_id=version.source_id,
                generation=version.generation,
                chunk_id=c.chunk_id,
                chunk_index=c.chunk_index,
                page_no=c.page_no,
                heading_path=c.heading_path,
                char_start=c.char_start,
                char_end=c.char_end,
                quote_text=c.quote_text,
                quote_hash=c.quote_hash,
                locator_kind=c.locator_kind,
                extraction_confidence=c.extraction_confidence,
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        return rows

    async def list_anchors(self, version_id: str) -> list[SourceAnchor]:
        result = await self.session.execute(
            select(SourceAnchor).where(
                SourceAnchor.source_version_id == version_id
            ).order_by(SourceAnchor.chunk_index)
        )
        return list(result.scalars().all())

    async def get_anchor(self, anchor_id: str) -> Optional[SourceAnchor]:
        return await self.session.get(SourceAnchor, anchor_id)

    async def get_anchors_by_ids(self, anchor_ids: Sequence[str]) -> list[SourceAnchor]:
        if not anchor_ids:
            return []
        result = await self.session.execute(
            select(SourceAnchor).where(SourceAnchor.id.in_(list(anchor_ids)))
        )
        return list(result.scalars().all())

    async def activate_version(self, version: KnowledgeSourceVersion) -> None:
        """激活一个版本；同源旧版本、旧产物、旧证据一起失效。"""
        await self.session.execute(
            update(KnowledgeSourceVersion).where(
                KnowledgeSourceVersion.source_type == version.source_type,
                KnowledgeSourceVersion.source_id == version.source_id,
                KnowledgeSourceVersion.id != version.id,
                KnowledgeSourceVersion.status != SourceVersionStatus.STALE,
            ).values(status=SourceVersionStatus.STALE)
        )
        version.status = SourceVersionStatus.ACTIVE
        await self.session.execute(
            update(KnowledgeArtifact).where(
                KnowledgeArtifact.source_type == version.source_type,
                KnowledgeArtifact.source_id == version.source_id,
                KnowledgeArtifact.source_version_id != version.id,
                KnowledgeArtifact.status == "active",
            ).values(status="stale")
        )
        await self.session.execute(
            update(KnowledgeEvidence).where(
                KnowledgeEvidence.source_type == version.source_type,
                KnowledgeEvidence.source_id == version.source_id,
                KnowledgeEvidence.source_version_id != version.id,
                KnowledgeEvidence.verification_status == EvidenceStatus.VERIFIED,
            ).values(verification_status=EvidenceStatus.STALE)
        )
        await self.session.flush()

    async def invalidate_source(self, *, owner_user_id: str, source_type: str,
                                source_id: str, status: str = SourceVersionStatus.STALE) -> None:
        await self.session.execute(
            update(KnowledgeSourceVersion).where(
                KnowledgeSourceVersion.owner_user_id == owner_user_id,
                KnowledgeSourceVersion.source_type == source_type,
                KnowledgeSourceVersion.source_id == source_id,
            ).values(status=status)
        )
        await self.session.execute(
            update(KnowledgeArtifact).where(
                KnowledgeArtifact.owner_user_id == owner_user_id,
                KnowledgeArtifact.source_type == source_type,
                KnowledgeArtifact.source_id == source_id,
            ).values(status="stale")
        )
        await self.session.execute(
            update(KnowledgeEvidence).where(
                KnowledgeEvidence.owner_user_id == owner_user_id,
                KnowledgeEvidence.source_type == source_type,
                KnowledgeEvidence.source_id == source_id,
            ).values(verification_status=EvidenceStatus.STALE)
        )
        await self.session.execute(
            update(KnowledgeGenerationJob).where(
                KnowledgeGenerationJob.owner_user_id == owner_user_id,
                KnowledgeGenerationJob.source_type == source_type,
                KnowledgeGenerationJob.source_id == source_id,
                KnowledgeGenerationJob.status.in_(("queued", "running")),
            ).values(status="stale")
        )
        await self.session.flush()

    # ---------- jobs ----------

    async def get_job(self, job_id: str) -> Optional[KnowledgeGenerationJob]:
        return await self.session.get(KnowledgeGenerationJob, job_id)

    async def get_job_for_version(self, version_id: str,
                                  artifact_type: str) -> Optional[KnowledgeGenerationJob]:
        result = await self.session.execute(
            select(KnowledgeGenerationJob).where(
                KnowledgeGenerationJob.source_version_id == version_id,
                KnowledgeGenerationJob.artifact_type == artifact_type,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_job(self, *, owner_user_id: str, version: KnowledgeSourceVersion,
                         artifact_type: str, prompt_version: str,
                         model_id: Optional[str], status: str = "queued",
                         reset: bool = False) -> KnowledgeGenerationJob:
        job = await self.get_job_for_version(version.id, artifact_type)
        if job is None:
            job = KnowledgeGenerationJob(
                id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                source_version_id=version.id,
                source_type=version.source_type,
                source_id=version.source_id,
                artifact_type=artifact_type,
                status=status,
                prompt_version=prompt_version,
                model_id=model_id,
            )
            self.session.add(job)
        else:
            job.owner_user_id = owner_user_id
            job.source_type = version.source_type
            job.source_id = version.source_id
            job.prompt_version = prompt_version
            job.model_id = model_id
            if reset or job.status in ("failed", "stale", "succeeded"):
                job.status = status
                job.error = None
                job.finished_at = None
        await self.session.flush()
        return job

    # ---------- artifacts / evidence ----------

    async def get_artifact(self, *, owner_user_id: str, source_type: str,
                           source_id: str, artifact_type: str,
                           active_only: bool = True) -> Optional[KnowledgeArtifact]:
        stmt = select(KnowledgeArtifact).where(
            KnowledgeArtifact.owner_user_id == owner_user_id,
            KnowledgeArtifact.source_type == source_type,
            KnowledgeArtifact.source_id == source_id,
            KnowledgeArtifact.artifact_type == artifact_type,
        )
        if active_only:
            stmt = stmt.where(KnowledgeArtifact.status == "active")
        result = await self.session.execute(
            stmt.order_by(KnowledgeArtifact.updated_at.desc()))
        return result.scalars().first()

    async def get_artifact_by_version(self, version_id: str,
                                      artifact_type: str) -> Optional[KnowledgeArtifact]:
        result = await self.session.execute(
            select(KnowledgeArtifact).where(
                KnowledgeArtifact.source_version_id == version_id,
                KnowledgeArtifact.artifact_type == artifact_type,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_artifact(self, *, owner_user_id: str, version: KnowledgeSourceVersion,
                              artifact_type: str, schema_version: str,
                              payload: dict, status: str = "active") -> KnowledgeArtifact:
        artifact = await self.get_artifact_by_version(version.id, artifact_type)
        if artifact is None:
            artifact = KnowledgeArtifact(
                id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                source_version_id=version.id,
                source_type=version.source_type,
                source_id=version.source_id,
                generation=version.generation,
                artifact_type=artifact_type,
                schema_version=schema_version,
                payload_json=payload,
                status=status,
            )
            self.session.add(artifact)
        else:
            artifact.schema_version = schema_version
            artifact.payload_json = payload
            artifact.status = status
        await self.session.flush()
        return artifact

    async def delete_evidence_for_artifact(self, artifact_id: str) -> None:
        await self.session.execute(
            delete(KnowledgeEvidence).where(
                KnowledgeEvidence.artifact_id == artifact_id)
        )

    async def delete_evidence_for_source_version(self, version_id: str,
                                                 target_kinds: Sequence[str] = ()) -> None:
        stmt = delete(KnowledgeEvidence).where(
            KnowledgeEvidence.source_version_id == version_id)
        if target_kinds:
            stmt = stmt.where(KnowledgeEvidence.target_kind.in_(list(target_kinds)))
        await self.session.execute(stmt)

    async def delete_evidence_for_targets(self, *, owner_user_id: str,
                                          target_kind: str,
                                          target_ids: Sequence[str]) -> None:
        if not target_ids:
            return
        await self.session.execute(
            delete(KnowledgeEvidence).where(
                KnowledgeEvidence.owner_user_id == owner_user_id,
                KnowledgeEvidence.target_kind == target_kind,
                KnowledgeEvidence.target_id.in_(list(target_ids)),
            )
        )

    async def add_evidence(self, **kwargs) -> KnowledgeEvidence:
        row = KnowledgeEvidence(id=str(uuid.uuid4()), **kwargs)
        self.session.add(row)
        return row

    async def list_evidence(self, artifact_id: str) -> list[KnowledgeEvidence]:
        result = await self.session.execute(
            select(KnowledgeEvidence).where(
                KnowledgeEvidence.artifact_id == artifact_id
            ).order_by(KnowledgeEvidence.target_kind, KnowledgeEvidence.target_id)
        )
        return list(result.scalars().all())

    async def list_evidence_for_target(self, *, owner_user_id: str,
                                       target_kind: str,
                                       target_id: str) -> list[KnowledgeEvidence]:
        result = await self.session.execute(
            select(KnowledgeEvidence).where(
                KnowledgeEvidence.owner_user_id == owner_user_id,
                KnowledgeEvidence.target_kind == target_kind,
                KnowledgeEvidence.target_id == target_id,
            ))
        return list(result.scalars().all())

    # ---------- concepts / relations ----------

    async def get_concept(self, *, owner_user_id: str, space_key: str,
                          normalized_name: str) -> Optional[KnowledgeConcept]:
        result = await self.session.execute(
            select(KnowledgeConcept).where(
                KnowledgeConcept.owner_user_id == owner_user_id,
                KnowledgeConcept.space_key == space_key,
                KnowledgeConcept.normalized_name == normalized_name,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_concept(self, *, owner_user_id: str, space_id: Optional[str],
                             canonical_name: str, normalized_name: str,
                             description: Optional[str], aliases: Optional[list],
                             ) -> KnowledgeConcept:
        space_key = space_id or ""
        concept = await self.get_concept(
            owner_user_id=owner_user_id, space_key=space_key,
            normalized_name=normalized_name)
        if concept is None:
            concept = KnowledgeConcept(
                id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                space_id=space_id or None,
                space_key=space_key,
                canonical_name=canonical_name[:200],
                normalized_name=normalized_name[:200],
                aliases_json=aliases or None,
                description=description,
                status="auto",
            )
            self.session.add(concept)
        else:
            concept.canonical_name = canonical_name[:200] or concept.canonical_name
            if description:
                concept.description = description
            if aliases:
                merged = list(dict.fromkeys((concept.aliases_json or []) + list(aliases)))
                concept.aliases_json = merged
        await self.session.flush()
        return concept

    async def get_concept_by_id(self, concept_id: str) -> Optional[KnowledgeConcept]:
        return await self.session.get(KnowledgeConcept, concept_id)

    async def get_relation(self, *, owner_user_id: str, space_key: str,
                           source_concept_id: str, target_concept_id: str,
                           relation_type: str) -> Optional[KnowledgeRelation]:
        result = await self.session.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.owner_user_id == owner_user_id,
                KnowledgeRelation.space_key == space_key,
                KnowledgeRelation.source_concept_id == source_concept_id,
                KnowledgeRelation.target_concept_id == target_concept_id,
                KnowledgeRelation.relation_type == relation_type,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_relation(self, *, owner_user_id: str, space_id: Optional[str],
                              source_concept_id: str, target_concept_id: str,
                              relation_type: str, description: Optional[str],
                              confidence: float = 1.0) -> KnowledgeRelation:
        space_key = space_id or ""
        relation = await self.get_relation(
            owner_user_id=owner_user_id, space_key=space_key,
            source_concept_id=source_concept_id,
            target_concept_id=target_concept_id,
            relation_type=relation_type)
        if relation is None:
            relation = KnowledgeRelation(
                id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                space_id=space_id or None,
                space_key=space_key,
                source_concept_id=source_concept_id,
                target_concept_id=target_concept_id,
                relation_type=relation_type,
                description=description,
                confidence=confidence,
                verification_state="auto",
            )
            self.session.add(relation)
        else:
            if description:
                relation.description = description
            relation.confidence = max(relation.confidence or 0.0, confidence)
        await self.session.flush()
        return relation

    async def get_relation_by_id(self, relation_id: str) -> Optional[KnowledgeRelation]:
        return await self.session.get(KnowledgeRelation, relation_id)

    async def list_concepts(self, *, owner_user_id: str, space_key: str,
                            query: Optional[str] = None, limit: int = 200,
                            ) -> list[KnowledgeConcept]:
        stmt = select(KnowledgeConcept).where(
            KnowledgeConcept.owner_user_id == owner_user_id,
            KnowledgeConcept.space_key == space_key,
            KnowledgeConcept.status != "rejected",
        )
        if query:
            like = f"%{query[:100]}%"
            stmt = stmt.where(
                (KnowledgeConcept.canonical_name.like(like))
                | (KnowledgeConcept.normalized_name.like(like)))
        result = await self.session.execute(
            stmt.order_by(KnowledgeConcept.updated_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def list_relations(self, *, owner_user_id: str, space_key: str,
                             limit: int = 500) -> list[KnowledgeRelation]:
        result = await self.session.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.owner_user_id == owner_user_id,
                KnowledgeRelation.space_key == space_key,
                KnowledgeRelation.verification_state != "rejected",
            ).order_by(KnowledgeRelation.updated_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_active_evidence_for_relations(self, relation_ids: Sequence[str],
                                                 ) -> list[KnowledgeEvidence]:
        if not relation_ids:
            return []
        result = await self.session.execute(
            select(KnowledgeEvidence).where(
                KnowledgeEvidence.target_kind == "relation",
                KnowledgeEvidence.target_id.in_(list(relation_ids)),
                KnowledgeEvidence.verification_status == EvidenceStatus.VERIFIED,
            )
        )
        return list(result.scalars().all())

    async def list_active_evidence_for_concepts(self, concept_ids: Sequence[str],
                                                ) -> list[KnowledgeEvidence]:
        if not concept_ids:
            return []
        result = await self.session.execute(
            select(KnowledgeEvidence).where(
                KnowledgeEvidence.target_kind == "concept",
                KnowledgeEvidence.target_id.in_(list(concept_ids)),
                KnowledgeEvidence.verification_status == EvidenceStatus.VERIFIED,
            )
        )
        return list(result.scalars().all())

    # ---------- mindmap user edits ----------

    async def list_mindmap_edits(self, *, owner_user_id: str, source_type: str,
                                 source_id: str) -> list[KnowledgeMindmapEdit]:
        result = await self.session.execute(
            select(KnowledgeMindmapEdit).where(
                KnowledgeMindmapEdit.owner_user_id == owner_user_id,
                KnowledgeMindmapEdit.source_type == source_type,
                KnowledgeMindmapEdit.source_id == source_id,
            ).order_by(KnowledgeMindmapEdit.updated_at)
        )
        return list(result.scalars().all())

    async def upsert_mindmap_edit(self, *, owner_user_id: str, source_type: str,
                                  source_id: str, base_source_version_id: str | None,
                                  node_id: str, label: str | None,
                                  sort_order: int | None) -> KnowledgeMindmapEdit:
        result = await self.session.execute(
            select(KnowledgeMindmapEdit).where(
                KnowledgeMindmapEdit.owner_user_id == owner_user_id,
                KnowledgeMindmapEdit.source_type == source_type,
                KnowledgeMindmapEdit.source_id == source_id,
                KnowledgeMindmapEdit.node_id == node_id,
            )
        )
        edit = result.scalar_one_or_none()
        if edit is None:
            edit = KnowledgeMindmapEdit(
                id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                source_type=source_type,
                source_id=source_id,
                base_source_version_id=base_source_version_id,
                node_id=node_id[:120],
                label=(label[:500] if label is not None else None),
                sort_order=sort_order,
            )
            self.session.add(edit)
        else:
            edit.base_source_version_id = base_source_version_id
            if label is not None:
                edit.label = label[:500]
            if sort_order is not None:
                edit.sort_order = sort_order
        await self.session.flush()
        return edit
