"""存量映射仓库（统一方案 C）——登记/查询/撤销映射。"""
import uuid
from typing import List

from sqlalchemy import select, update

from app.models.legacy_mapping import LegacyDocumentMapping, LegacyMappingStatus


class LegacyMappingRepository:
    def __init__(self, session):
        self.session = session

    async def upsert(self, *, source: str, legacy_vector_id: str, owner: str,
                     document_id: str, compat_generation: int = 0,
                     source_batch: str = None, evidence_summary: str = None,
                     status: str = LegacyMappingStatus.ACTIVE) -> LegacyDocumentMapping:
        """按 (source, legacy_vector_id) 幂等登记；重复 upsert 不产生重复行。"""
        existing = (await self.session.execute(
            select(LegacyDocumentMapping).where(
                LegacyDocumentMapping.source == source,
                LegacyDocumentMapping.legacy_vector_id == legacy_vector_id,
            )
        )).scalar_one_or_none()
        if existing:
            # 同身份重放：完整映射内容（代次/批次/证据/归属）一致才视为幂等，
            # 不修改任何字段；代次、批次或归属变化拒绝（评审：非无副作用幂等）
            from app.models.legacy_mapping import LegacyMappingStatus
            if existing.status == LegacyMappingStatus.REVOKED.value:
                raise ConflictError(
                    f"legacy_vector_id {legacy_vector_id[:40]} 已被撤销，重放不得自动激活")
            unchanged = (
                existing.owner == owner
                and existing.document_id == document_id
                and int(existing.compat_generation or 0) == int(compat_generation or 0)
                and (existing.source_batch or "") == (source_batch or "")
                and (existing.evidence_summary or "") == (evidence_summary or "")
            )
            if unchanged:
                return existing  # 完整一致重放：无副作用
            existing.status = LegacyMappingStatus.UNRESOLVED  # 内容/归属变化：保留原数据待人工
            await self.session.flush()  # 冲突登记：编排层决定提交时机，不隐式提交调用方工作
            raise ConflictError(
                f"legacy_vector_id {legacy_vector_id[:40]} 已映射到 "
                f"{existing.document_id}（owner {existing.owner}），归属变化被拒绝")
        row = LegacyDocumentMapping(
            id=str(uuid.uuid4()),
            source=source, legacy_vector_id=legacy_vector_id,
            owner=owner, document_id=document_id,
            compat_generation=compat_generation, status=status,
            source_batch=source_batch, evidence_summary=evidence_summary,
        )
        self.session.add(row)
        await self.session.flush()
        return row


        row = LegacyDocumentMapping(
            id=str(uuid.uuid4()),
            source=source, legacy_vector_id=legacy_vector_id,
            owner=owner, document_id=document_id,
            compat_generation=compat_generation, status=status,
            source_batch=source_batch, evidence_summary=evidence_summary,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_active_for_owner(self, source: str, owner: str,
                                   legacy_ids: List[str]) -> dict:
        """按数据源 + owner + 旧 ID 批量查询 active 映射 → document_id。"""
        return {lid: entry[0] for lid, entry in
                (await self.get_active_for_owner_with_generation(
                    source, owner, legacy_ids)).items()}

    async def get_active_for_owner_with_generation(
            self, source: str, owner: str, legacy_ids: List[str]) -> dict:
        """查询 active 映射 → (document_id, compat_generation)（复验修正：
        读取端须独立校验映射代次与当前 active 代次）。"""
        if not legacy_ids:
            return {}
        result = await self.session.execute(
            select(LegacyDocumentMapping.legacy_vector_id,
                   LegacyDocumentMapping.document_id,
                   LegacyDocumentMapping.compat_generation)
            .where(
                LegacyDocumentMapping.source == source,
                LegacyDocumentMapping.owner == owner,
                LegacyDocumentMapping.legacy_vector_id.in_(legacy_ids),
                LegacyDocumentMapping.status == LegacyMappingStatus.ACTIVE,
            )
        )
        return {lid: (doc_id, gen or 0) for lid, doc_id, gen in result.all()}

    async def revoke(self, source: str, owner: str, document_id: str) -> int:
        """文档发布新代次后撤销其旧映射（旧映射不再作为 active 正文返回）。"""
        result = await self.session.execute(
            update(LegacyDocumentMapping)
            .where(LegacyDocumentMapping.source == source)
            .where(LegacyDocumentMapping.owner == owner)
            .where(LegacyDocumentMapping.document_id == document_id)
            .values(status=LegacyMappingStatus.REVOKED)
        )
        return result.rowcount

    async def list_unresolved(self, source: str = None, limit: int = 100) -> List[LegacyDocumentMapping]:
        conds = [LegacyDocumentMapping.status == LegacyMappingStatus.UNRESOLVED]
        if source:
            conds.append(LegacyDocumentMapping.source == source)
        result = await self.session.execute(
            select(LegacyDocumentMapping).where(*conds).limit(limit))
        return list(result.scalars().all())


class ConflictError(Exception):
    """映射归属冲突：保留原数据，拒绝覆盖。"""
