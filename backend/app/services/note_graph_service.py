"""笔记知识图谱查询（V2 P4）。

节点 = 当前用户可读的笔记；边 = 正文显式链接 + 手动关联。
不读取笔记正文用于图构建，不做 AI/embedding 依赖，不按标题合并节点。
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import func, or_, select

from app.models.note import Note
from app.models.note_graph import (
    NoteLinkOccurrence, NoteLinkStatus, NoteManualRelation,
)


def _node_from_row(row, degree: dict | None = None) -> dict:
    stats = degree or {}
    return {
        "id": row.id,
        "title": row.title,
        "category": row.category,
        "tags": row.tags or [],
        "updated_at": str(row.updated_at) if row.updated_at else None,
        "degree": stats.get("degree", 0),
        "in_degree": stats.get("in_degree", 0),
        "out_degree": stats.get("out_degree", 0),
        "explicit_count": stats.get("explicit", 0),
        "manual_count": stats.get("manual", 0),
        "is_orphan": stats.get("degree", 0) == 0,
    }


async def _note_rows(db, owner_user_id: str, note_ids: list[str]):
    if not note_ids:
        return {}
    rows = await db.execute(
        select(Note.id, Note.title, Note.category, Note.tags, Note.updated_at)
        .where(Note.user_id == owner_user_id, Note.id.in_(note_ids))
    )
    return {row.id: row for row in rows.all()}


async def _explicit_edge_rows(db, owner_user_id: str, note_ids: set[str]):
    if not note_ids:
        return []
    rows = await db.execute(
        select(
            NoteLinkOccurrence.source_note_id,
            NoteLinkOccurrence.target_note_id,
            func.count(NoteLinkOccurrence.id).label("occurrence_count"),
        ).where(
            NoteLinkOccurrence.owner_user_id == owner_user_id,
            NoteLinkOccurrence.status == NoteLinkStatus.RESOLVED,
            NoteLinkOccurrence.target_note_id.isnot(None),
            NoteLinkOccurrence.source_note_id.in_(list(note_ids)),
            NoteLinkOccurrence.target_note_id.in_(list(note_ids)),
        ).group_by(
            NoteLinkOccurrence.source_note_id,
            NoteLinkOccurrence.target_note_id,
        )
    )
    return rows.all()


async def _manual_relation_rows(db, owner_user_id: str, note_ids: set[str]):
    if not note_ids:
        return []
    rows = await db.execute(
        select(NoteManualRelation).where(
            NoteManualRelation.owner_user_id == owner_user_id,
            NoteManualRelation.source_note_id.in_(list(note_ids)),
            NoteManualRelation.target_note_id.in_(list(note_ids)),
        )
    )
    return list(rows.scalars().all())


def _build_edges(explicit_rows, manual_rows, edge_type: str):
    edges = []
    if edge_type in ("all", "explicit"):
        for row in explicit_rows:
            edges.append({
                "id": f"explicit:{row.source_note_id}:{row.target_note_id}",
                "source": row.source_note_id,
                "target": row.target_note_id,
                "type": "explicit_link",
                "count": int(row.occurrence_count),
            })
    if edge_type in ("all", "manual"):
        for relation in manual_rows:
            edges.append({
                "id": f"manual:{relation.id}",
                "relation_id": relation.id,
                "source": relation.source_note_id,
                "target": relation.target_note_id,
                "type": "manual_relation",
                "count": 1,
                "reason": relation.reason,
                "created_at": str(relation.created_at) if relation.created_at else None,
            })
    return edges


def _degree_map(explicit_rows, manual_rows) -> dict[str, dict]:
    stats: dict[str, dict] = {}

    def entry(note_id: str) -> dict:
        return stats.setdefault(note_id, {
            "degree": 0, "in_degree": 0, "out_degree": 0,
            "explicit": 0, "manual": 0,
        })

    for row in explicit_rows:
        count = int(row.occurrence_count)
        source = entry(row.source_note_id)
        target = entry(row.target_note_id)
        source["out_degree"] += count
        source["degree"] += count
        source["explicit"] += count
        target["in_degree"] += count
        target["degree"] += count
        target["explicit"] += count
    for relation in manual_rows:
        source = entry(relation.source_note_id)
        target = entry(relation.target_note_id)
        source["out_degree"] += 1
        source["degree"] += 1
        source["manual"] += 1
        target["in_degree"] += 1
        target["degree"] += 1
        target["manual"] += 1
    return stats


async def get_note_graph(
    db,
    *,
    owner_user_id: str,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    category: Optional[str] = None,
    edge_type: str = "all",
    include_orphans: bool = True,
    limit: int = 300,
) -> dict:
    edge_type = edge_type if edge_type in ("all", "explicit", "manual") else "all"
    limit = max(1, min(int(limit), 1000))

    conditions = [Note.user_id == owner_user_id]
    if q:
        conditions.append(Note.title.ilike(f"%{q[:100]}%"))
    if category:
        conditions.append(Note.category == category)
    if tag:
        # Apply exact JSON membership before COUNT/LIMIT. A fixed prefetch window
        # cannot guarantee that an older matching note will be found.
        if db.get_bind().dialect.name == "sqlite":
            values = func.json_each(Note.tags).table_valued("key", "value")
            conditions.append(select(1).select_from(values).where(
                values.c.value == tag).exists())
        else:
            conditions.append(func.json_contains(Note.tags, json.dumps(tag)) == 1)

    total = (await db.execute(
        select(func.count(Note.id)).where(*conditions)
    )).scalar() or 0

    rows = (await db.execute(
        select(Note.id, Note.title, Note.category, Note.tags, Note.updated_at)
        .where(*conditions)
        .order_by(Note.updated_at.desc())
        .limit(limit + 1)
    )).all()
    truncated = len(rows) > limit
    rows = rows[:limit]
    note_ids = {row.id for row in rows}

    explicit_rows = await _explicit_edge_rows(db, owner_user_id, note_ids)
    manual_rows = await _manual_relation_rows(db, owner_user_id, note_ids)
    degree = _degree_map(explicit_rows, manual_rows)
    nodes = [_node_from_row(row, degree.get(row.id)) for row in rows]
    if not include_orphans:
        nodes = [node for node in nodes if not node["is_orphan"]]
        visible = {node["id"] for node in nodes}
        explicit_rows = [row for row in explicit_rows
                         if row.source_note_id in visible and row.target_note_id in visible]
        manual_rows = [row for row in manual_rows
                       if row.source_note_id in visible and row.target_note_id in visible]
    edges = _build_edges(explicit_rows, manual_rows, edge_type)
    return {
        "scope": "global",
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "visible_notes": len(nodes),
            "visible_edges": len(edges),
            "total_notes": int(total),
            "truncated": bool(truncated or total > len(rows)),
        },
        "filters": {
            "q": q, "tag": tag, "category": category,
            "edge_type": edge_type, "include_orphans": include_orphans,
            "limit": limit,
        },
    }


async def get_local_graph(
    db,
    *,
    owner_user_id: str,
    note_id: str,
    depth: int = 1,
    limit: int = 200,
) -> Optional[dict]:
    depth = 2 if int(depth) >= 2 else 1
    limit = max(1, min(int(limit), 500))
    center = (await _note_rows(db, owner_user_id, [note_id])).get(note_id)
    if center is None:
        return None

    visited: set[str] = {note_id}
    frontier: set[str] = {note_id}
    truncated = False
    for _ in range(depth):
        if not frontier:
            break
        explicit = await db.execute(
            select(NoteLinkOccurrence.source_note_id,
                   NoteLinkOccurrence.target_note_id).where(
                NoteLinkOccurrence.owner_user_id == owner_user_id,
                NoteLinkOccurrence.status == NoteLinkStatus.RESOLVED,
                NoteLinkOccurrence.target_note_id.isnot(None),
                or_(
                    NoteLinkOccurrence.source_note_id.in_(list(frontier)),
                    NoteLinkOccurrence.target_note_id.in_(list(frontier)),
                ),
            )
        )
        manual = await db.execute(
            select(NoteManualRelation.source_note_id,
                   NoteManualRelation.target_note_id).where(
                NoteManualRelation.owner_user_id == owner_user_id,
                or_(
                    NoteManualRelation.source_note_id.in_(list(frontier)),
                    NoteManualRelation.target_note_id.in_(list(frontier)),
                ),
            )
        )
        neighbors: set[str] = set()
        for source, target in list(explicit.all()) + list(manual.all()):
            neighbors.add(source)
            neighbors.add(target)
        new_ids = neighbors - visited
        room = limit - len(visited)
        if room <= 0:
            truncated = True
            break
        if len(new_ids) > room:
            new_ids = set(list(new_ids)[:room])
            truncated = True
        visited |= new_ids
        frontier = new_ids

    existing = await _note_rows(db, owner_user_id, list(visited))
    visible = set(existing)
    explicit_rows = await _explicit_edge_rows(db, owner_user_id, visible)
    manual_rows = await _manual_relation_rows(db, owner_user_id, visible)
    degree = _degree_map(explicit_rows, manual_rows)
    nodes = [_node_from_row(row, degree.get(row.id)) for row in existing.values()]
    edges = _build_edges(explicit_rows, manual_rows, "all")
    return {
        "scope": "local",
        "center_id": note_id,
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "visible_notes": len(nodes),
            "visible_edges": len(edges),
            "truncated": truncated,
        },
    }


def _occurrence_payload(row) -> dict:
    return {
        "occurrence_id": row.id,
        "raw_token": row.raw_token,
        "display_text": row.display_text,
        "char_start": row.char_start,
        "char_end": row.char_end,
        "context_text": row.context_text,
        "status": row.status,
        "source_revision": row.source_revision,
    }


async def get_note_links(db, *, owner_user_id: str,
                         note_id: str) -> Optional[dict]:
    center = (await _note_rows(db, owner_user_id, [note_id])).get(note_id)
    if center is None:
        return None

    outgoing_rows = list((await db.execute(
        select(NoteLinkOccurrence)
        .where(NoteLinkOccurrence.owner_user_id == owner_user_id,
               NoteLinkOccurrence.source_note_id == note_id)
        .order_by(NoteLinkOccurrence.char_start)
    )).scalars().all())
    incoming_rows = list((await db.execute(
        select(NoteLinkOccurrence)
        .where(NoteLinkOccurrence.owner_user_id == owner_user_id,
               NoteLinkOccurrence.target_note_id == note_id,
               NoteLinkOccurrence.status == NoteLinkStatus.RESOLVED)
        .order_by(NoteLinkOccurrence.created_at)
    )).scalars().all())
    manual_rows = list((await db.execute(
        select(NoteManualRelation).where(
            NoteManualRelation.owner_user_id == owner_user_id,
            or_(NoteManualRelation.source_note_id == note_id,
                NoteManualRelation.target_note_id == note_id),
        )
    )).scalars().all())

    related_ids = {row.target_note_id for row in outgoing_rows if row.target_note_id}
    related_ids |= {row.source_note_id for row in incoming_rows}
    for relation in manual_rows:
        related_ids.add(relation.source_note_id)
        related_ids.add(relation.target_note_id)
    related_ids.discard(note_id)
    related = await _note_rows(db, owner_user_id, list(related_ids))

    outgoing: dict[str, list[dict]] = {}
    unresolved = []
    for row in outgoing_rows:
        if row.target_note_id and row.target_note_id in related:
            outgoing.setdefault(row.target_note_id, []).append(_occurrence_payload(row))
        else:
            unresolved.append(_occurrence_payload(row))

    incoming: dict[str, list[dict]] = {}
    for row in incoming_rows:
        if row.source_note_id in related:
            incoming.setdefault(row.source_note_id, []).append(_occurrence_payload(row))

    def grouped(mapping: dict[str, list[dict]]) -> list[dict]:
        result = []
        for related_id, occurrences in mapping.items():
            row = related[related_id]
            result.append({
                "note": _node_from_row(row),
                "occurrences": occurrences,
                "count": len(occurrences),
            })
        result.sort(key=lambda item: item["note"]["title"] or "")
        return result

    manual = []
    for relation in manual_rows:
        direction = "outgoing" if relation.source_note_id == note_id else "incoming"
        other_id = (relation.target_note_id if direction == "outgoing"
                    else relation.source_note_id)
        row = related.get(other_id)
        if row is None:
            continue
        manual.append({
            "relation_id": relation.id,
            "direction": direction,
            "relation_type": relation.relation_type,
            "reason": relation.reason,
            "created_at": str(relation.created_at) if relation.created_at else None,
            "note": _node_from_row(row),
        })

    out_count = sum(len(items) for items in outgoing.values())
    in_count = sum(len(items) for items in incoming.values())
    return {
        "note": _node_from_row(center, {
            "degree": out_count + in_count + len(manual),
            "out_degree": out_count + sum(item["direction"] == "outgoing" for item in manual),
            "in_degree": in_count + sum(item["direction"] == "incoming" for item in manual),
            "explicit": out_count + in_count,
            "manual": len(manual),
        }),
        "outgoing": grouped(outgoing),
        "incoming": grouped(incoming),
        "manual": manual,
        "unresolved": unresolved,
        "counts": {
            "outgoing_notes": len(outgoing),
            "incoming_notes": len(incoming),
            "manual": len(manual),
            "unresolved": len(unresolved),
        },
    }


async def link_candidates(db, *, owner_user_id: str, q: str = "",
                          limit: int = 10,
                          exclude_note_id: Optional[str] = None) -> list[dict]:
    limit = max(1, min(int(limit), 30))
    stmt = select(Note.id, Note.title, Note.category, Note.tags, Note.updated_at).where(
        Note.user_id == owner_user_id)
    if exclude_note_id:
        stmt = stmt.where(Note.id != exclude_note_id)
    if q and q.strip():
        stmt = stmt.where(Note.title.ilike(f"%{q.strip()[:100]}%"))
    rows = (await db.execute(
        stmt.order_by(Note.updated_at.desc()).limit(limit * 2)
    )).all()

    titles = {(row.title or "").casefold() for row in rows}
    duplicate_map: dict[str, int] = {}
    if titles:
        dup_rows = await db.execute(
            select(func.lower(Note.title), func.count(Note.id))
            .where(Note.user_id == owner_user_id,
                   func.lower(Note.title).in_(list(titles)))
            .group_by(func.lower(Note.title))
        )
        duplicate_map = {name: int(count) for name, count in dup_rows.all()}

    result = []
    for row in rows[:limit]:
        result.append({
            "id": row.id,
            "title": row.title,
            "category": row.category,
            "tags": row.tags or [],
            "updated_at": str(row.updated_at) if row.updated_at else None,
            "duplicate_title": duplicate_map.get((row.title or "").casefold(), 0) > 1,
        })
    return result
