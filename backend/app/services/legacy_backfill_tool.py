"""
存量盘点与安全回填工具（统一方案 C）。

命令：inventory / dry-run / apply / status / resume / rollback。
默认只读；apply 需显式 source/owner 范围与批次 ID；dry-run 零数据源写入。
"""
import asyncio
from datetime import datetime, timezone
from typing import List, Optional



def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def inventory(vectors_store, *, user_id: str, page_size: int = 100) -> dict:
    """分页盘点用户向量：分类旧随机/固定/MD5 代次/document_id 代次/损坏。"""
    stats = {"random_ids": 0, "fixed_ids": 0, "md5_gen_ids": 0,
             "doc_gen_ids": 0, "corrupt": 0}
    samples = []
    offset = 0
    while True:
        got = await asyncio.to_thread(
            vectors_store.get,
            where={"user_id": user_id},
            include=["metadatas"],
            limit=page_size, offset=offset,
        ) if hasattr(vectors_store, "get") else None
        if got is None or not got.get("ids"):
            break
        ids = got["ids"]
        metas = got.get("metadatas", [])
        for i, lid in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            if not isinstance(meta, dict) or not meta.get("document_id"):
                if "document_id" not in meta:
                    stats["corrupt" if "md5" not in meta else "md5_gen_ids"] += 1
                else:
                    stats["doc_gen_ids"] += 1
            elif "generation" in meta:
                stats["doc_gen_ids"] += 1
            elif lid.startswith("kb:"):
                stats["fixed_ids"] += 1
            else:
                stats["random_ids"] += 1
            if len(samples) < 10:
                samples.append({"id": lid[:60], "meta_keys": list(meta.keys())[:6]})
        offset += len(ids)
        if len(ids) < page_size:
            break
    return {"user_id": user_id, "stats": stats, "samples": samples}


async def dry_run(mappings: List[dict]) -> dict:
    """dry-run：零数据源写入，输出拟改动摘要与数量对账。"""
    return {
        "dry_run": True,
        "proposed": len(mappings),
        "by_document": {m["document_id"]: m.get("compat_generation", 0)
                        for m in mappings},
        "conflicts": [m for m in mappings if m.get("conflict")],
    }


async def apply(repo, *, source: str, user_id: str, mappings: List[dict],
                batch: str, evidence_summary: str) -> dict:
    """apply：按 (source, legacy_vector_id) 幂等登记映射；同批次重放不重复。"""
    applied = 0
    conflicts = []
    for m in mappings:
        try:
            await repo.upsert(
                source=source, legacy_vector_id=m["legacy_vector_id"],
                owner=user_id, document_id=m["document_id"],
                compat_generation=m.get("compat_generation", 0),
                source_batch=batch, evidence_summary=evidence_summary,
            )
            applied += 1
        except Exception as e:
            conflicts.append({"legacy_vector_id": m["legacy_vector_id"], "error": str(e)[:120]})
    return {"applied": applied, "conflicts": len(conflicts), "batch": batch}


async def status(repo, *, source: Optional[str] = None, limit: int = 100) -> dict:
    """查看映射状态与未解析清单。"""
    unresolved = await repo.list_unresolved(source=source, limit=limit)
    return {"unresolved_count": len(unresolved),
            "unresolved": [{"id": u.id, "source": u.source,
                            "legacy_vector_id": u.legacy_vector_id[:60],
                            "owner": u.owner} for u in unresolved]}


async def resume(repo, *, source: str, batch: str, limit: int = 100) -> dict:
    """resume：续跑未完成批次（持久进度）。

    - 以 source_batch 为幂等键统计已完成/撤销映射，不重复登记；
    - 返回批次内 active 数量（未完成项由调用方对目标清单 diff 后补登记）。
    """
    from sqlalchemy import select, func
    from app.models.legacy_mapping import LegacyDocumentMapping, LegacyMappingStatus
    total = (await repo.session.execute(
        select(func.count()).where(
            LegacyDocumentMapping.source == source,
            LegacyDocumentMapping.source_batch == batch,
        ))).scalar_one()
    active = (await repo.session.execute(
        select(func.count()).where(
            LegacyDocumentMapping.source == source,
            LegacyDocumentMapping.source_batch == batch,
            LegacyDocumentMapping.status == LegacyMappingStatus.ACTIVE.value,
        ))).scalar_one()
    return {"batch": batch, "total_in_batch": int(total or 0),
            "active_in_batch": int(active or 0),
            "needs_work": int((total or 0) - (active or 0))}


async def rollback(repo, *, source: str, batch: str, owner: str) -> dict:
    """rollback：仅撤销本批且未被后续操作改动的映射（版本保护）。

    - 只删除 source_batch == batch 的行：被后续批次改写 source_batch 的
      映射不在本批（避免撤销后续工作）；
    - 标记而非物理删除原向量：撤销只移除映射，旧向量保留待重新归属；
    - 记录撤销批次（source_batch 保留，供审计追溯）。
    """
    from sqlalchemy import update
    from app.models.legacy_mapping import (
        LegacyDocumentMapping, LegacyMappingStatus)
    # 版本保护 + 审计：命中行置 revoked（保留 source_batch 供审计），不 DELETE
    result = await repo.session.execute(
        update(LegacyDocumentMapping)
        .where(LegacyDocumentMapping.source == source)
        .where(LegacyDocumentMapping.source_batch == batch)
        .where(LegacyDocumentMapping.owner == owner)
        .where(LegacyDocumentMapping.status != LegacyMappingStatus.REVOKED.value)
        .values(status=LegacyMappingStatus.REVOKED.value)
    )
    await repo.session.commit()
    return {"revoked": result.rowcount, "batch": batch, "owner": owner}
