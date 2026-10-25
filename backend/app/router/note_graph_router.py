"""笔记知识图谱 API（V2 P4）。

前缀使用 `/notes`，与既有 `/note` CRUD 路由隔离，避免 `/{note_id}` 动态匹配
吞掉 `/graph`、`/link-candidates` 等静态路径。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.db.db_config import get_db
from app.services.note_graph_service import (
    get_local_graph, get_note_graph, get_note_links, link_candidates,
)
from app.services.note_link_service import add_manual_relation, remove_manual_relation
from app.utils.auth_utils import get_current_user_id

note_graph_router = APIRouter(prefix="/notes", tags=["note-graph"])


class ManualRelationCreate(BaseModel):
    target_note_id: str = Field(min_length=1, max_length=36)
    reason: Optional[str] = Field(default=None, max_length=1000)


@note_graph_router.get("/graph")
async def note_graph(
    q: Optional[str] = Query(None, description="标题搜索"),
    tag: Optional[str] = Query(None, description="标签筛选"),
    category: Optional[str] = Query(None, description="分类筛选"),
    edge_type: str = Query("all", pattern="^(all|explicit|manual)$"),
    include_orphans: bool = Query(True, description="是否显示孤立笔记"),
    limit: int = Query(300, ge=1, le=1000),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=60, window=60)),
):
    data = await get_note_graph(
        db, owner_user_id=user_id, q=q, tag=tag, category=category,
        edge_type=edge_type, include_orphans=include_orphans, limit=limit)
    return success_response(data=data)


@note_graph_router.get("/link-candidates")
async def get_link_candidates(
    q: str = Query("", max_length=100),
    limit: int = Query(10, ge=1, le=30),
    exclude_note_id: Optional[str] = Query(None),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=120, window=60)),
):
    data = await link_candidates(
        db, owner_user_id=user_id, q=q, limit=limit,
        exclude_note_id=exclude_note_id)
    return success_response(data={"candidates": data, "total": len(data)})


@note_graph_router.get("/{note_id}/local-graph")
async def note_local_graph(
    note_id: str,
    depth: int = Query(1, ge=1, le=2),
    limit: int = Query(200, ge=1, le=500),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    data = await get_local_graph(
        db, owner_user_id=user_id, note_id=note_id, depth=depth, limit=limit)
    if data is None:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return success_response(data=data)


@note_graph_router.get("/{note_id}/links")
async def note_links(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    data = await get_note_links(db, owner_user_id=user_id, note_id=note_id)
    if data is None:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return success_response(data=data)


@note_graph_router.post("/{note_id}/manual-relations")
async def create_manual_relation(
    note_id: str,
    payload: ManualRelationCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=30, window=60)),
):
    try:
        relation = await add_manual_relation(
            db, owner_user_id=user_id, source_note_id=note_id,
            target_note_id=payload.target_note_id, reason=payload.reason,
            created_by=user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="笔记不存在")
    except ValueError:
        raise HTTPException(status_code=400, detail="不能关联笔记自身")
    await db.commit()
    return success_response(data={
        "relation_id": relation.id,
        "source_note_id": relation.source_note_id,
        "target_note_id": relation.target_note_id,
        "relation_type": relation.relation_type,
        "reason": relation.reason,
    })


@note_graph_router.delete("/{note_id}/manual-relations/{relation_id}")
async def delete_manual_relation(
    note_id: str,
    relation_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    removed = await remove_manual_relation(
        db, owner_user_id=user_id, note_id=note_id, relation_id=relation_id)
    if not removed:
        raise HTTPException(status_code=404, detail="关联不存在")
    await db.commit()
    return success_response(message="关联已移除")
