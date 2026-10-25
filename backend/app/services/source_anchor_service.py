"""来源锚点抽取、证据核验与失效。

N0 的核心约束：
- 只记录解析器真实提供的页码/章节/偏移；信息不足时降级为 chunk 定位，
  绝不编造页码；
- 锚点绑定 owner + source_id + generation，读取与核验都复核当前 active 代次；
- 引用核验以真实原文为唯一依据，模型输出不能自证。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from app.models.knowledge_insight import (
    KnowledgeSourceVersion, SourceType, SourceVersionStatus,
)
from app.repositories.knowledge_insight_repository import KnowledgeInsightRepository

PARSER_VERSION = "anchor-v1"
MAX_QUOTE_CHARS = 8000

_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")


@dataclass(frozen=True)
class AnchorCandidate:
    chunk_index: int
    chunk_id: Optional[str]
    page_no: Optional[int]
    heading_path: Optional[str]
    char_start: Optional[int]
    char_end: Optional[int]
    quote_text: str
    quote_hash: str
    locator_kind: str
    extraction_confidence: float


@dataclass
class _Segment:
    text: str
    page_no: Optional[int]
    headings: list[tuple[int, str]]
    cursor: int = 0
    norm_text: Optional[str] = None
    norm_map: Optional[list[int]] = None


# ---------------------------------------------------------------------------
# 纯函数：可独立单测，不依赖数据库/向量库
# ---------------------------------------------------------------------------

def normalize_quote(text: str) -> str:
    """压缩空白以便比对；不改变字符内容顺序。"""
    return re.sub(r"\s+", " ", text or "").strip()


def hash_quote(text: str) -> str:
    return hashlib.sha256(normalize_quote(text).encode("utf-8")).hexdigest()


def page_from_metadata(metadata: Optional[dict]) -> Optional[int]:
    """解析加载器真实页码。

    - `page_number` / `slide_number`：1 起（Unstructured、PPT 幻灯片）；
    - `page`：LangChain PyPDFLoader 为 0 起，统一转换为 1 起。
    两者都没有时返回 None，不猜测。
    """
    if not metadata:
        return None
    for key in ("page_number", "slide_number"):
        value = metadata.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    value = metadata.get("page")
    if value is not None:
        try:
            return int(value) + 1
        except (TypeError, ValueError):
            return None
    return None


def markdown_headings(text: str) -> list[tuple[int, str]]:
    """返回 (字符偏移, 章节路径) 列表。"""
    stack: dict[int, str] = {}
    headings: list[tuple[int, str]] = []
    for match in _HEADING_RE.finditer(text or ""):
        level = len(match.group(1))
        title = match.group(2).strip()
        stack[level] = title
        for existing in list(stack):
            if existing > level:
                del stack[existing]
        path = " > ".join(stack[key] for key in sorted(stack) if stack[key])
        headings.append((match.start(), path))
    return headings


def _metadata_headings(metadata: Optional[dict]) -> list[tuple[int, str]]:
    """把加载器记录的真实标题样式转换为 (偏移, 层级路径)。"""
    raw = (metadata or {}).get("headings")
    if not isinstance(raw, list):
        return []
    entries: list[tuple[int, int, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        try:
            offset = int(item.get("char_start"))
        except (TypeError, ValueError):
            continue
        if offset < 0:
            continue
        try:
            level = max(1, int(item.get("level") or 1))
        except (TypeError, ValueError):
            level = 1
        entries.append((offset, level, title))
    entries.sort(key=lambda entry: entry[0])

    stack: dict[int, str] = {}
    headings: list[tuple[int, str]] = []
    for offset, level, title in entries:
        stack[level] = title
        for existing in list(stack):
            if existing > level:
                del stack[existing]
        path = " > ".join(stack[key] for key in sorted(stack) if stack[key])
        headings.append((offset, path))
    return headings


def _merge_headings(*groups: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    merged: list[tuple[int, str]] = []
    for group in groups:
        merged.extend(group)
    merged.sort(key=lambda pair: pair[0])
    return merged


def _heading_at(headings: Sequence[tuple[int, str]], offset: int) -> Optional[str]:
    found = None
    for position, path in headings:
        if position <= offset:
            found = path
        else:
            break
    return found


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    mapping: list[int] = []
    previous_space = False
    leading = True
    for index, char in enumerate(text):
        if char.isspace():
            if previous_space or leading:
                continue
            chars.append(" ")
            mapping.append(index)
            previous_space = True
        else:
            chars.append(char)
            mapping.append(index)
            previous_space = False
            leading = False
    while chars and chars[-1] == " ":
        chars.pop()
        mapping.pop()
    return "".join(chars), mapping


def _find_offsets(segment: _Segment, quote: str, cursor: int) -> Optional[tuple[int, int]]:
    text = segment.text
    exact = text.find(quote, max(0, cursor))
    if exact >= 0:
        return exact, exact + len(quote)
    stripped = quote.strip()
    if stripped and stripped != quote:
        exact = text.find(stripped, max(0, cursor))
        if exact >= 0:
            return exact, exact + len(stripped)

    if segment.norm_text is None:
        segment.norm_text, segment.norm_map = _normalized_with_map(text)
    normalized_quote = normalize_quote(quote)
    if not normalized_quote or not segment.norm_text:
        return None
    search_from = 0
    while True:
        hit = segment.norm_text.find(normalized_quote, search_from)
        if hit < 0:
            return None
        original_start = segment.norm_map[hit]
        original_end = segment.norm_map[hit + len(normalized_quote) - 1] + 1
        if original_start >= cursor:
            return original_start, original_end
        search_from = hit + 1


def _build_segments(source_documents: Sequence) -> list[_Segment]:
    segments: list[_Segment] = []
    for document in source_documents or []:
        text = getattr(document, "page_content", "") or ""
        metadata = getattr(document, "metadata", None) or {}
        headings = _merge_headings(
            markdown_headings(text), _metadata_headings(metadata))
        segments.append(_Segment(
            text=text,
            page_no=page_from_metadata(metadata),
            headings=headings,
        ))
    return segments


def _locator_kind(page_no: Optional[int], char_start: Optional[int],
                  heading_path: Optional[str]) -> str:
    if page_no is not None:
        return "page"
    if char_start is not None:
        return "char"
    if heading_path:
        return "section"
    return "chunk"


def build_anchor_candidates(source_documents: Sequence,
                            split_documents: Sequence,
                            chunk_ids: Optional[Sequence[Optional[str]]] = None,
                            ) -> list[AnchorCandidate]:
    """从“原始页/段落”与“切分结果”生成锚点候选。

    切分器按源文档顺序产出切片并复制 metadata，因此可以用页码或顺序
    在源文本中定位切片。定位失败时保留真实引文，但 char/page 留空。
    """
    chunks = list(split_documents or [])
    if not chunks:
        return []

    ids = list(chunk_ids or [])
    segments = _build_segments(source_documents)

    if not segments:
        candidates: list[AnchorCandidate] = []
        for index, chunk in enumerate(chunks):
            text = getattr(chunk, "page_content", "") or ""
            metadata = getattr(chunk, "metadata", None) or {}
            page_no = page_from_metadata(metadata)
            quote = text[:MAX_QUOTE_CHARS]
            candidates.append(AnchorCandidate(
                chunk_index=index,
                chunk_id=ids[index] if index < len(ids) else None,
                page_no=page_no,
                heading_path=None,
                char_start=None,
                char_end=None,
                quote_text=quote,
                quote_hash=hash_quote(quote),
                locator_kind=_locator_kind(page_no, None, None),
                extraction_confidence=0.4,
            ))
        return candidates

    by_page: dict[int, list[int]] = {}
    for index, segment in enumerate(segments):
        if segment.page_no is not None:
            by_page.setdefault(segment.page_no, []).append(index)

    candidates = []
    last_segment_index = 0
    for index, chunk in enumerate(chunks):
        text = getattr(chunk, "page_content", "") or ""
        metadata = getattr(chunk, "metadata", None) or {}
        page_no = page_from_metadata(metadata)

        order: list[int] = []
        if page_no is not None and page_no in by_page:
            order.extend(by_page[page_no])
        if last_segment_index not in order:
            order.append(last_segment_index)
        order.extend(i for i in range(len(segments)) if i not in order)

        located: Optional[tuple[int, int, int]] = None
        for segment_index in order:
            segment = segments[segment_index]
            found = _find_offsets(segment, text, segment.cursor)
            if found is not None:
                located = (segment_index, found[0], found[1])
                break

        if located is not None:
            segment_index, char_start, char_end = located
            segment = segments[segment_index]
            segment.cursor = char_start + 1
            last_segment_index = segment_index
            heading_path = _heading_at(segment.headings, char_start)
            effective_page = page_no if page_no is not None else segment.page_no
            confidence = 1.0
        else:
            segment_index = (by_page[page_no][0] if page_no is not None
                             and by_page.get(page_no) else last_segment_index)
            segment = segments[segment_index]
            char_start = char_end = None
            heading_path = None
            effective_page = page_no if page_no is not None else segment.page_no
            confidence = 0.5 if effective_page is not None else 0.3

        quote = text[:MAX_QUOTE_CHARS]
        candidates.append(AnchorCandidate(
            chunk_index=index,
            chunk_id=ids[index] if index < len(ids) else None,
            page_no=effective_page,
            heading_path=heading_path,
            char_start=char_start,
            char_end=char_end,
            quote_text=quote,
            quote_hash=hash_quote(quote),
            locator_kind=_locator_kind(effective_page, char_start, heading_path),
            extraction_confidence=confidence,
        ))
    return candidates


def split_note_blocks(text: str, chunk_size: int = 800,
                      overlap: int = 100) -> list[tuple[int, int]]:
    """按段落聚合笔记文本，返回 (start, end) 偏移；不伪造页码。"""
    if not text:
        return []
    paragraphs: list[tuple[int, int]] = []
    cursor = 0
    for block in re.split(r"(\n\s*\n)", text):
        if not block:
            continue
        if block.strip():
            start = cursor
            end = cursor + len(block)
            paragraphs.append((start, end))
        cursor += len(block)

    ranges: list[tuple[int, int]] = []
    if not paragraphs:
        return []
    start, end = paragraphs[0]
    for next_start, next_end in paragraphs[1:]:
        if next_end - start <= chunk_size:
            end = next_end
            continue
        ranges.append((start, end))
        start = max(end - overlap, next_start)
        end = next_end
    ranges.append((start, end))
    return ranges


def build_note_anchor_candidates(text: str) -> list[AnchorCandidate]:
    headings = markdown_headings(text or "")
    candidates = []
    for index, (start, end) in enumerate(split_note_blocks(text or "")):
        quote = (text[start:end] or "")[:MAX_QUOTE_CHARS]
        heading_path = _heading_at(headings, start)
        candidates.append(AnchorCandidate(
            chunk_index=index,
            chunk_id=None,
            page_no=None,
            heading_path=heading_path,
            char_start=start,
            char_end=end,
            quote_text=quote,
            quote_hash=hash_quote(quote),
            locator_kind=_locator_kind(None, start, heading_path),
            extraction_confidence=1.0,
        ))
    return candidates


# ---------------------------------------------------------------------------
# 数据库编排
# ---------------------------------------------------------------------------

async def record_document_anchors(
    session,
    *,
    owner_user_id: str,
    space_id: Optional[str],
    document_id: str,
    generation: int,
    content_hash: Optional[str],
    source_documents: Sequence,
    split_documents: Sequence,
    chunk_ids: Optional[Sequence[Optional[str]]] = None,
) -> Optional[KnowledgeSourceVersion]:
    candidates = build_anchor_candidates(
        source_documents, split_documents, chunk_ids)
    if not candidates:
        return None
    repo = KnowledgeInsightRepository(session)
    version = await repo.upsert_version(
        owner_user_id=owner_user_id,
        space_id=space_id,
        source_type=SourceType.DOCUMENT,
        source_id=document_id,
        generation=generation,
        content_hash=content_hash,
        status=SourceVersionStatus.PENDING,
        chunk_count=len(candidates),
        parser_version=PARSER_VERSION,
    )
    await repo.replace_anchors(version, candidates)
    return version


async def record_note_anchors(
    session,
    *,
    note_id: str,
    owner_user_id: str,
    note_text: str,
) -> Optional[KnowledgeSourceVersion]:
    candidates = build_note_anchor_candidates(note_text or "")
    if not candidates:
        return None
    repo = KnowledgeInsightRepository(session)
    latest = await repo.get_latest_version(
        owner_user_id=owner_user_id,
        source_type=SourceType.NOTE,
        source_id=note_id,
    )
    generation = (latest.generation + 1) if latest else 1
    version = await repo.upsert_version(
        owner_user_id=owner_user_id,
        space_id=None,
        source_type=SourceType.NOTE,
        source_id=note_id,
        generation=generation,
        content_hash=hashlib.sha256((note_text or "").encode("utf-8")).hexdigest(),
        status=SourceVersionStatus.PENDING,
        chunk_count=len(candidates),
        parser_version=PARSER_VERSION,
    )
    await repo.replace_anchors(version, candidates)
    # 笔记保存后立即生效；旧版本/旧产物/旧证据同时失效
    await repo.activate_version(version)
    return version


async def activate_document_version(session, *, owner_user_id: str,
                                    document_id: str, generation: int) -> None:
    repo = KnowledgeInsightRepository(session)
    version = await repo.get_version(
        source_type=SourceType.DOCUMENT, source_id=document_id,
        generation=generation)
    if version is None:
        return
    if version.owner_user_id != owner_user_id:
        return
    await repo.activate_version(version)


async def invalidate_document(session, *, owner_user_id: str,
                              document_id: str, invalid: bool = False) -> None:
    repo = KnowledgeInsightRepository(session)
    await repo.invalidate_source(
        owner_user_id=owner_user_id,
        source_type=SourceType.DOCUMENT,
        source_id=document_id,
        status=(SourceVersionStatus.INVALID if invalid
                else SourceVersionStatus.STALE),
    )


async def invalidate_note(session, *, owner_user_id: str, note_id: str) -> None:
    repo = KnowledgeInsightRepository(session)
    await repo.invalidate_source(
        owner_user_id=owner_user_id,
        source_type=SourceType.NOTE,
        source_id=note_id,
        status=SourceVersionStatus.INVALID,
    )


@dataclass(frozen=True)
class AnchorVerification:
    anchor: object
    verified: bool
    reason: str
    quote_hash: Optional[str] = None


async def verify_anchor(session, *, owner_user_id: str, anchor_id: str,
                        quote: Optional[str] = None) -> AnchorVerification:
    """读取锚点并复核 owner、active 版本、当前 active_generation 与引文。"""
    from app.models.document_index import DocumentIndex
    from app.models.note import Note

    repo = KnowledgeInsightRepository(session)
    anchor = await repo.get_anchor(anchor_id)
    if anchor is None:
        return AnchorVerification(anchor=None, verified=False, reason="anchor_not_found")
    if anchor.owner_user_id != owner_user_id:
        return AnchorVerification(anchor=None, verified=False, reason="forbidden")

    version = await repo.get_version_by_id(anchor.source_version_id)
    if version is None or version.status != SourceVersionStatus.ACTIVE:
        return AnchorVerification(anchor=anchor, verified=False, reason="stale_version")
    if version.owner_user_id != owner_user_id:
        return AnchorVerification(anchor=None, verified=False, reason="forbidden")

    if anchor.source_type == SourceType.DOCUMENT:
        document = await session.get(DocumentIndex, anchor.source_id)
        if document is None or document.user_id != owner_user_id:
            return AnchorVerification(anchor=None, verified=False, reason="forbidden")
        if int(document.active_generation or 0) != int(anchor.generation):
            return AnchorVerification(anchor=anchor, verified=False,
                                      reason="generation_superseded")
    elif anchor.source_type == SourceType.NOTE:
        note = await session.get(Note, anchor.source_id)
        if note is None or note.user_id != owner_user_id:
            return AnchorVerification(anchor=None, verified=False, reason="forbidden")
    else:
        return AnchorVerification(anchor=None, verified=False, reason="unknown_source")

    if quote is not None:
        normalized = normalize_quote(quote)
        if not normalized or normalized not in normalize_quote(anchor.quote_text):
            return AnchorVerification(anchor=anchor, verified=False,
                                      reason="quote_mismatch",
                                      quote_hash=hash_quote(quote))
        return AnchorVerification(anchor=anchor, verified=True, reason="verified",
                                  quote_hash=hash_quote(quote))
    return AnchorVerification(anchor=anchor, verified=True, reason="verified",
                              quote_hash=anchor.quote_hash)
