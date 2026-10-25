"""笔记内部链接解析与持久化（V2 P3）。

- 只解析 Markdown 正文中的 `[[...]]`，跳过代码围栏、行内代码与转义；
- `[[note:<uuid>|显示文字]]` 使用稳定 ID；
- `[[标题]]` / `[[标题|别名]]` 仅在当前用户可读范围内唯一匹配时绑定，
  重名标记 ambiguous，不任取第一条；
- 每次保存幂等替换该笔记的全部 occurrence，不做增量猜测。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update

from app.core.logger_handler import logger
from app.models.note import Note
from app.models.note_graph import (
    NoteLinkOccurrence, NoteLinkStatus, NoteManualRelation,
)

_LINK_RE = re.compile(r"\[\[([^\[\]\n]{1,300})\]\]")
_ID_LINK_RE = re.compile(
    r"^note:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:\|(.*))?$", re.S)
_FENCED_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


@dataclass(frozen=True)
class ParsedLink:
    raw_token: str
    display_text: str
    target_kind: str  # id / title
    target_value: str
    char_start: int
    char_end: int
    context_text: str


def _mask_code(text: str) -> str:
    """用空格替换代码区域，保留换行与字符偏移。"""
    chars = list(text)

    def mask(start: int, end: int) -> None:
        for index in range(start, min(end, len(chars))):
            if chars[index] != "\n":
                chars[index] = " "

    for match in _FENCED_RE.finditer(text):
        mask(match.start(), match.end())
    masked = "".join(chars)
    for match in _INLINE_CODE_RE.finditer(masked):
        mask(match.start(), match.end())
    return "".join(chars)


def _context_at(text: str, start: int, end: int, limit: int = 240) -> str:
    left = text.rfind("\n\n", 0, start)
    left = 0 if left < 0 else left + 2
    right = text.find("\n\n", end)
    right = len(text) if right < 0 else right
    snippet = text[left:right].strip()
    if len(snippet) <= limit:
        return snippet
    center = max(0, start - left)
    half = limit // 2
    begin = max(0, center - half)
    return snippet[begin:begin + limit]


def extract_links(content: str) -> list[ParsedLink]:
    """纯函数：从 Markdown 正文提取链接（不含数据库解析）。"""
    text = content or ""
    if "[[" not in text:
        return []
    masked = _mask_code(text)
    links: list[ParsedLink] = []
    for match in _LINK_RE.finditer(masked):
        if match.start() > 0 and masked[match.start() - 1] == "\\":
            continue
        inner = text[match.start(1):match.end(1)].strip()
        if not inner:
            continue
        id_match = _ID_LINK_RE.match(inner)
        if id_match:
            target_kind = "id"
            target_value = id_match.group(1)
            alias = (id_match.group(2) or "").strip()
            display = alias or target_value
        else:
            parts = inner.split("|", 1)
            target_kind = "title"
            target_value = parts[0].strip()
            display = (parts[1].strip() if len(parts) > 1 and parts[1].strip()
                       else target_value)
            if not target_value:
                continue
        links.append(ParsedLink(
            raw_token=text[match.start():match.end()],
            display_text=display[:300],
            target_kind=target_kind,
            target_value=target_value,
            char_start=match.start(),
            char_end=match.end(),
            context_text=_context_at(text, match.start(), match.end()),
        ))
    return links


async def _resolve_targets(db, owner_user_id: str,
                           parsed: list[ParsedLink]) -> dict[str, tuple[str | None, str]]:
    """返回 parser key -> (target_note_id, status)。"""
    result: dict[str, tuple[str | None, str]] = {}

    titles = {
        link.target_value.casefold()
        for link in parsed if link.target_kind == "title"
    }
    by_title: dict[str, list[str]] = {}
    if titles:
        rows = await db.execute(
            select(Note.id, Note.title).where(
                Note.user_id == owner_user_id,
                func.lower(Note.title).in_(list(titles)),
            )
        )
        for note_id, title in rows.all():
            by_title.setdefault((title or "").casefold(), []).append(note_id)

    ids = {link.target_value for link in parsed if link.target_kind == "id"}
    existing_ids: set[str] = set()
    if ids:
        rows = await db.execute(
            select(Note.id).where(
                Note.user_id == owner_user_id, Note.id.in_(list(ids)))
        )
        existing_ids = set(rows.scalars().all())

    for link in parsed:
        key = f"{link.target_kind}:{link.target_value.casefold()}"
        if key in result:
            continue
        if link.target_kind == "id":
            if link.target_value in existing_ids:
                result[key] = (link.target_value, NoteLinkStatus.RESOLVED)
            else:
                result[key] = (None, NoteLinkStatus.TARGET_DELETED)
        else:
            matches = by_title.get(link.target_value.casefold(), [])
            if len(matches) == 1:
                result[key] = (matches[0], NoteLinkStatus.RESOLVED)
            elif len(matches) > 1:
                result[key] = (None, NoteLinkStatus.AMBIGUOUS)
            else:
                result[key] = (None, NoteLinkStatus.UNRESOLVED)
    return result


async def sync_note_links(db, note: Note) -> int:
    """Replace occurrences while preserving established bindings of unchanged tokens.

    Positions and paragraph context may change on an ordinary edit. A title is
    only used to resolve a new link, never to retarget an already resolved or
    deleted link. The caller owns the transaction.
    """
    # An older save can finish its asynchronous side work after a newer save.
    # Lock/read persisted content, not the session's potentially stale ORM copy.
    current = (await db.execute(select(Note.content).where(
        Note.id == note.id, Note.user_id == note.user_id,
    ).with_for_update())).first()
    if current is None or (current.content or "") != (note.content or ""):
        return 0
    parsed = extract_links(note.content or "")
    previous = (await db.execute(
        select(NoteLinkOccurrence).where(
            NoteLinkOccurrence.owner_user_id == note.user_id,
            NoteLinkOccurrence.source_note_id == note.id,
        ).order_by(NoteLinkOccurrence.char_start, NoteLinkOccurrence.id)
    )).scalars().all()
    bindings = defaultdict(deque)
    for occurrence in previous:
        bindings[occurrence.raw_token].append(
            (occurrence.target_note_id, occurrence.status))
    bound_ids = {row.target_note_id for row in previous if row.target_note_id}
    readable_ids = set((await db.execute(select(Note.id).where(
        Note.user_id == note.user_id, Note.id.in_(bound_ids)
    ))).scalars().all()) if bound_ids else set()
    await db.execute(
        delete(NoteLinkOccurrence).where(
            NoteLinkOccurrence.owner_user_id == note.user_id,
            NoteLinkOccurrence.source_note_id == note.id,
        )
    )
    if not parsed:
        await db.flush()
        return 0

    resolved = await _resolve_targets(db, note.user_id, parsed)
    revision = hashlib.sha256((note.content or "").encode("utf-8")).hexdigest()
    for link in parsed:
        target_id, status = resolved.get(
            f"{link.target_kind}:{link.target_value.casefold()}",
            (None, NoteLinkStatus.UNRESOLVED),
        )
        if bindings[link.raw_token]:
            old_target, old_status = bindings[link.raw_token].popleft()
            if old_status == NoteLinkStatus.TARGET_DELETED:
                target_id, status = None, NoteLinkStatus.TARGET_DELETED
            elif old_status == NoteLinkStatus.RESOLVED:
                target_id = old_target if old_target in readable_ids else None
                status = (NoteLinkStatus.RESOLVED if target_id
                          else NoteLinkStatus.TARGET_DELETED)
        db.add(NoteLinkOccurrence(
            id=str(uuid.uuid4()),
            owner_user_id=note.user_id,
            source_note_id=note.id,
            source_revision=revision,
            target_note_id=target_id,
            raw_token=link.raw_token[:500],
            display_text=link.display_text[:300],
            char_start=link.char_start,
            char_end=link.char_end,
            context_text=link.context_text,
            status=status,
        ))
    await db.flush()
    return len(parsed)


async def remove_note_links(db, note_id: str, owner_user_id: str) -> None:
    """删除笔记后：删除其出链，目标失效但保留来源 occurrence 供 UI 标记。"""
    await db.execute(
        delete(NoteLinkOccurrence).where(
            NoteLinkOccurrence.owner_user_id == owner_user_id,
            NoteLinkOccurrence.source_note_id == note_id,
        )
    )
    await db.execute(
        update(NoteLinkOccurrence).where(
            NoteLinkOccurrence.owner_user_id == owner_user_id,
            NoteLinkOccurrence.target_note_id == note_id,
        ).values(target_note_id=None, status=NoteLinkStatus.TARGET_DELETED)
    )
    await db.execute(
        delete(NoteManualRelation).where(
            NoteManualRelation.owner_user_id == owner_user_id,
            (NoteManualRelation.source_note_id == note_id)
            | (NoteManualRelation.target_note_id == note_id),
        )
    )
    await db.flush()


async def add_manual_relation(db, *, owner_user_id: str, source_note_id: str,
                              target_note_id: str, reason: str | None = None,
                              created_by: str | None = None) -> NoteManualRelation:
    if source_note_id == target_note_id:
        raise ValueError("self_relation")
    rows = await db.execute(
        select(Note.id).where(
            Note.user_id == owner_user_id,
            Note.id.in_([source_note_id, target_note_id]),
        )
    )
    if len(set(rows.scalars().all())) != 2:
        raise LookupError("note_not_found")
    existing = await db.execute(
        select(NoteManualRelation).where(
            NoteManualRelation.owner_user_id == owner_user_id,
            NoteManualRelation.source_note_id == source_note_id,
            NoteManualRelation.target_note_id == target_note_id,
            NoteManualRelation.relation_type == "manual",
        )
    )
    relation = existing.scalar_one_or_none()
    if relation is not None:
        if reason:
            relation.reason = reason
        return relation
    relation = NoteManualRelation(
        id=str(uuid.uuid4()),
        owner_user_id=owner_user_id,
        source_note_id=source_note_id,
        target_note_id=target_note_id,
        relation_type="manual",
        reason=reason,
        created_by=created_by or owner_user_id,
    )
    db.add(relation)
    await db.flush()
    return relation


async def remove_manual_relation(db, *, owner_user_id: str, note_id: str,
                                 relation_id: str) -> bool:
    result = await db.execute(
        delete(NoteManualRelation).where(
            NoteManualRelation.id == relation_id,
            NoteManualRelation.owner_user_id == owner_user_id,
            (NoteManualRelation.source_note_id == note_id)
            | (NoteManualRelation.target_note_id == note_id),
        )
    )
    await db.flush()
    return bool(result.rowcount)


async def sync_note_links_safe(db, note: Note) -> None:
    """保存链路使用的容错包装：链接失败不阻断笔记保存。"""
    try:
        await sync_note_links(db, note)
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.warning("【笔记链接】解析/保存失败（笔记已保存）: %s", str(exc)[:200])
