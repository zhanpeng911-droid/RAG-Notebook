"""
笔记管理 API 路由 —— CRUD、搜索、自动标签、内联补全、写作辅助。
"""
from fastapi.routing import APIRouter
from fastapi import Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from app.services.note_service import note_service
from app.schemas.models import (
    NoteCreate,
    NoteUpdate,
    NoteListResponse,
)
from app.utils.auth_utils import get_current_user_id
from app.core.success_response import success_response
from app.core.exceptions import NoteNotFoundException
from app.core.rate_limit import rate_limit
from app.db.db_config import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.validator import get_settings
from app.core.logger_handler import logger

note_router = APIRouter(prefix="/note", tags=["note"])


def _enqueue_note_vector_sync(note_id: str, user_id: str) -> None:
    """Best-effort queue submission; never delay a completed MySQL save."""
    if not get_settings().NOTE_VECTOR_INDEX_ENABLED:
        return
    try:
        from app.tasks.celery_app import sync_note_vector_task
        sync_note_vector_task.delay(note_id, user_id)
    except Exception as exc:
        logger.warning(f"Vector sync task was not submitted for {note_id}: {exc}")


def _enqueue_note_vector_delete(note_id: str, user_id: str) -> None:
    """Best-effort asynchronous vector cleanup."""
    if not get_settings().NOTE_VECTOR_INDEX_ENABLED:
        return
    try:
        from app.tasks.celery_app import delete_note_vector_task
        delete_note_vector_task.delay(note_id, user_id)
    except Exception as exc:
        logger.warning(f"Vector delete task was not submitted for {note_id}: {exc}")



@note_router.post("/create")
async def create_note(
    payload: NoteCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """Create a note quickly; non-critical AI work is queued after commit."""
    llm_config = payload.llm_config if isinstance(payload.llm_config, dict) else (payload.llm_config.model_dump() if payload.llm_config else None)
    note = await note_service.create_note(db, user_id, payload, llm_config=llm_config)
    logger.info(f"Note created user_id={user_id}, note_id={note.id}")

    _enqueue_note_vector_sync(note.id, user_id)
    if get_settings().NOTE_AUTO_TAG_ENABLED and not payload.category and not payload.tags:
        try:
            from app.tasks.celery_app import generate_tags_task
            generate_tags_task.delay(note.id, user_id, payload.content, llm_config)
        except Exception as exc:
            # Do not fall back to in-process LLM work: it can monopolize FastAPI
            # while a local Ollama endpoint is unavailable.
            logger.warning(f"Auto-tag task was not submitted for {note.id}: {exc}")

    return success_response(message="Note created", data=note)


@note_router.get("/list")
async def list_notes(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str = Query(None),
    tag: str = Query(None),
):
    """
    笔记列表：分页查询，支持按分类筛选。tag 筛选在内存层完成。
    """
    notes, total = await note_service.list_notes(db, user_id, page, page_size, category, tag)
    return success_response(data=NoteListResponse(notes=notes, total_count=total))


@note_router.get("/search")
async def search_notes(
    q: str = Query(..., description="搜索关键词"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    全文语义搜索：走 ChromaDB notes_collection 向量检索，
    返回当前用户的语义相似笔记。
    """
    notes = await note_service.search_notes(db, user_id, q)
    return success_response(data=NoteListResponse(notes=notes, total_count=len(notes)))


@note_router.get("/related")
async def get_related_notes_by_query(
    q: str = Query(..., description="查询文本"),
    top_k: int = Query(5, ge=1, le=20),
    user_id: str = Depends(get_current_user_id),
):
    """根据查询文本检索相关笔记（AI 对话"相关笔记"标签页使用）"""
    notes = await note_service.search_related_notes(q, user_id, top_k)
    return success_response(data={"notes": notes, "total": len(notes)})


@note_router.get("/stats")
async def get_stats(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取用户笔记分类统计。
    返回各分类下的笔记数量及总数。
    """
    stats = await note_service.get_category_stats(db, user_id)
    return success_response(data=stats)


class AutocompleteRequest(BaseModel):
    """内联补全请求模型"""
    context: str
    llm_config: Optional[dict] = None


@note_router.post("/autocomplete")
async def autocomplete(
    payload: AutocompleteRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    AI 内联补全。基于光标前上下文
    """
    llm_config = payload.llm_config if isinstance(payload.llm_config, dict) else (payload.llm_config.model_dump() if payload.llm_config else None)
    result = await note_service.autocomplete(payload.context, llm_config=llm_config)
    return success_response(data=result)


class AssistRequest(BaseModel):
    """写作辅助请求模型"""
    content: str
    action: str = "continue"
    llm_config: Optional[dict] = None


@note_router.post("/assist/stream")
async def assist_stream(
    payload: AssistRequest,
    user_id: str = Depends(get_current_user_id),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """
    AI 写作辅助 SSE 流式输出。支持三种模式：
    - continue：续写
    - expand：扩写
    - summarize：缩写
    """
    llm_config = payload.llm_config if isinstance(payload.llm_config, dict) else (payload.llm_config.model_dump() if payload.llm_config else None)
    return StreamingResponse(
        note_service.assist_stream(payload.content, payload.action, llm_config=llm_config),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@note_router.put("/{note_id}")
async def update_note(
    note_id: str,
    payload: NoteUpdate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """Save MySQL changes first and refresh the semantic index in Celery."""
    note = await note_service.update_note(db, note_id, user_id, payload)
    if not note:
        raise NoteNotFoundException()
    if {"title", "content"}.intersection(payload.model_fields_set):
        _enqueue_note_vector_sync(note_id, user_id)
    return success_response(message="Note updated", data=note)


@note_router.delete("/{note_id}")
async def delete_note(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """Delete MySQL data immediately and clean the vector store asynchronously."""
    deleted = await note_service.delete_note(db, note_id, user_id)
    if not deleted:
        raise NoteNotFoundException()
    _enqueue_note_vector_delete(note_id, user_id)
    return success_response(message="Note deleted")


@note_router.get("/{note_id}")
async def get_note(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取笔记详情。
    """
    note = await note_service.get_note(db, note_id, user_id)
    if not note:
        raise NoteNotFoundException()
    return success_response(data=note)


@note_router.post("/{note_id}/auto-tag")
async def regenerate_tags(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    手动触发重新生成标签。
    """
    note = await note_service.get_note(db, note_id, user_id)
    if not note:
        raise NoteNotFoundException()

    try:
        from app.tasks.celery_app import generate_tags_task
        generate_tags_task.delay(note_id, user_id, note.content)
    except Exception:
        import asyncio
        asyncio.create_task(note_service._auto_tag_and_review(note_id, user_id, note.content))
    return success_response(message="标签生成任务已提交")


@note_router.get("/{note_id}/related")
async def get_related_notes(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前笔记的语义相似笔记和知识库文档（Top 3），
    标注来源：note（笔记库）或 knowledge_base（知识库）。
    """
    related = await note_service.get_related_notes(db, note_id, user_id)
    return success_response(data=related)


@note_router.get("/{note_id}/export")
async def export_note(
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    导出单篇笔记为 Markdown 格式纯文本。
    """
    md = await note_service.export_note_markdown(db, note_id, user_id)
    if not md:
        raise NoteNotFoundException()
    return success_response(data={"markdown": md, "filename": f"{note_id}.md"})
