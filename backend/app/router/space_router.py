"""
空间管理 API 路由 —— 组织下的知识库空间 CRUD。
"""
from fastapi import APIRouter, Depends, Path, Query, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.space import Space
from app.models.space_document import SpaceDocument
from app.models.organization import Organization
from app.models.note import Note
from app.utils.auth_utils import get_current_user_id
from app.db.db_config import get_db
from app.core.success_response import success_response
from app.core.exceptions import OrganizationNotFoundException, SpaceNotFoundException
from app.core.permission import _get_user_role
from app.core.audit import write_audit_log
from app.rag.vector_store import VectorStoreService

space_router = APIRouter(prefix="/space", tags=["space"])


class SpaceCreate(BaseModel):
    """创建空间的请求体。"""
    org_id: str
    name: str
    description: str = ""


class SpaceUpdate(BaseModel):
    """更新空间信息的请求体，所有字段可选。"""
    name: Optional[str] = None
    description: Optional[str] = None


def _space_to_dict(space: Space, doc_count: int | None = None) -> dict:
    """
    将空间模型对象转换为字典格式。

    参数:
        space (Space): 空间 ORM 对象。
        doc_count (int | None, optional): 该空间下的文档数量，为 None 时不包含该字段。

    返回:
        dict: 包含 id、space_id、org_id、name、description、created_by、created_at 的字典，
             可选包含 doc_count。
    """
    created_at = space.__dict__.get("created_at")
    data = {
        "id": space.id,
        "space_id": space.id,
        "org_id": space.org_id,
        "name": space.name,
        "description": space.description,
        "created_by": space.created_by,
        "created_at": str(created_at) if created_at else None,
    }
    if doc_count is not None:
        data["doc_count"] = doc_count
    return data


async def _count_space_documents(db: AsyncSession, space_id: str) -> int:
    """
    统计空间内的文档总数（笔记 + 知识库文档）。

    参数:
        db (AsyncSession): 异步数据库会话。
        space_id (str): 空间 ID。

    返回:
        int: 空间内的笔记数量与知识库文档数量之和。

    关键逻辑:
        - 笔记数量通过 SpaceDocument 表查询 resource_type="note" 的记录数。
        - 知识库文档数量通过 VectorStoreService 查询，查询失败时记为 0。
    """
    result = await db.execute(
        select(func.count(SpaceDocument.id)).where(
            SpaceDocument.space_id == space_id,
            SpaceDocument.resource_type == "note",
        )
    )
    note_count = result.scalar() or 0
    async_doc_count = 0
    try:
        store = VectorStoreService()
        docs = await store.get_user_documents(None, space_id=space_id)
        async_doc_count = len(docs)
    except Exception:
        async_doc_count = 0
    return note_count + async_doc_count


async def _get_space_for_member(db: AsyncSession, space_id: str, user_id: str) -> tuple[Space, str]:
    """
    获取空间对象并验证用户是否为该空间所属组织的成员。

    参数:
        db (AsyncSession): 异步数据库会话。
        space_id (str): 空间 ID。
        user_id (str): 当前用户 ID。

    返回:
        tuple: 包含 Space 对象和用户在该组织中的角色字符串。

    异常:
        SpaceNotFoundException: 空间不存在时抛出。
        HTTPException: 用户不是该组织成员时抛出 403。
    """
    result = await db.execute(select(Space).where(Space.id == space_id))
    space = result.scalar_one_or_none()
    if not space:
        raise SpaceNotFoundException()

    role = await _get_user_role(db, space.org_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="您不是该组织的成员")
    return space, role


def _note_document_to_dict(space_doc: SpaceDocument, note: Note) -> dict:
    """
    将空间文档和笔记模型对象组合转换为字典格式。

    参数:
        space_doc (SpaceDocument): 空间文档 ORM 对象，包含空间关联信息。
        note (Note): 笔记 ORM 对象，包含笔记内容。

    返回:
        dict: 包含空间文档 ID、笔记 ID、标题、内容、预览（截取前160字）、
             标签、分类、所有者 ID、添加者、创建和更新时间的字典。
    """
    content = note.content or ""
    return {
        "id": space_doc.id,
        "space_document_id": space_doc.id,
        "resource_type": "note",
        "resource_id": note.id,
        "note_id": note.id,
        "title": note.title,
        "content": content,
        "preview": content[:160] + ("..." if len(content) > 160 else ""),
        "tags": note.tags,
        "category": note.category,
        "owner_id": note.user_id,
        "added_by": space_doc.added_by,
        "created_at": str(space_doc.__dict__.get("created_at")) if space_doc.__dict__.get("created_at") else None,
        "updated_at": str(note.updated_at) if note.updated_at else None,
    }


@space_router.post("/create")
async def create_space(
    payload: SpaceCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    创建知识库空间。

    需要组织的 owner 或 admin 权限。创建时会验证组织是否存在以及用户权限。

    参数:
        payload (SpaceCreate): 创建空间的请求体，包含 org_id、name 和 description。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含新创建的空间信息和 doc_count=0。

    异常:
        HTTPException: 空间名称为空时抛出 400。
        HTTPException: 权限不足时抛出 403。
        OrganizationNotFoundException: 组织不存在时抛出。
    """
    space_name = payload.name.strip()
    if not space_name:
        raise HTTPException(status_code=400, detail="空间名称不能为空")

    # 校验组织存在
    org_stmt = select(Organization).where(Organization.id == payload.org_id)
    org_result = await db.execute(org_stmt)
    if not org_result.scalar_one_or_none():
        raise OrganizationNotFoundException()

    # 手动校验权限（因为 org_id 在请求体中，不在路径中）
    role = await _get_user_role(db, payload.org_id, user_id)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="权限不足，需要 owner 或 admin 角色")

    space = Space(
        org_id=payload.org_id,
        name=space_name,
        description=payload.description or "",
        created_by=user_id,
    )
    db.add(space)
    await db.flush()

    await write_audit_log(db, user_id, "create", "space", space.id,
                          org_id=payload.org_id, detail={"name": space_name})
    await db.commit()

    return success_response(message="空间创建成功", data=_space_to_dict(space, doc_count=0))


@space_router.get("/list")
async def list_spaces(
    org_id: str = Query(..., description="组织ID"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定组织下的所有空间列表。

    仅组织成员可查看，按创建时间倒序排列，每个空间附带文档数量。

    参数:
        org_id (str): 组织 ID，必填。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 spaces 列表和 total 数量。

    异常:
        HTTPException: 用户不是该组织成员时抛出 403。
    """
    role = await _get_user_role(db, org_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="您不是该组织的成员")

    stmt = (
        select(Space)
        .where(Space.org_id == org_id)
        .order_by(Space.created_at.desc())
    )
    result = await db.execute(stmt)
    space_models = result.scalars().all()
    spaces = []
    for space in space_models:
        spaces.append(_space_to_dict(space, doc_count=await _count_space_documents(db, space.id)))

    return success_response(data={"spaces": spaces, "total": len(spaces)})


@space_router.get("/{space_id}")
async def get_space(
    space_id: str = Path(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定空间的详细信息。

    仅组织成员可查看，返回空间基本信息和文档数量。

    参数:
        space_id (str): 空间 ID，从路径参数获取。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含空间详情和 doc_count。

    异常:
        SpaceNotFoundException: 空间不存在时抛出。
        HTTPException: 用户不是该组织成员时抛出 403。
    """
    stmt = select(Space).where(Space.id == space_id)
    result = await db.execute(stmt)
    space = result.scalar_one_or_none()
    if not space:
        raise SpaceNotFoundException()

    role = await _get_user_role(db, space.org_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="您不是该组织的成员")

    # 查询该空间下的文档数量
    doc_count = await _count_space_documents(db, space_id)

    return success_response(data=_space_to_dict(space, doc_count=doc_count))


@space_router.get("/{space_id}/available-notes")
async def list_available_notes_for_space(
    space_id: str = Path(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    列出当前用户可加入该空间的、尚未加入的自己的笔记。

    查询当前用户的所有笔记，排除已在该空间中的笔记，最多返回 200 条。

    参数:
        space_id (str): 空间 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 notes 列表（含 id、标题、预览、标签、分类）和 total 数量。
    """
    await _get_space_for_member(db, space_id, user_id)

    existing_result = await db.execute(
        select(SpaceDocument.resource_id).where(
            SpaceDocument.space_id == space_id,
            SpaceDocument.resource_type == "note",
        )
    )
    existing_note_ids = set(existing_result.scalars().all())

    stmt = (
        select(Note)
        .where(Note.user_id == user_id)
        .order_by(Note.updated_at.desc())
        .limit(200)
    )
    result = await db.execute(stmt)
    notes = []
    for note in result.scalars().all():
        if note.id in existing_note_ids:
            continue
        content = note.content or ""
        notes.append({
            "id": note.id,
            "note_id": note.id,
            "title": note.title,
            "preview": content[:120] + ("..." if len(content) > 120 else ""),
            "tags": note.tags,
            "category": note.category,
            "updated_at": str(note.updated_at) if note.updated_at else None,
        })

    return success_response(data={"notes": notes, "total": len(notes)})


@space_router.get("/{space_id}/documents")
async def list_space_documents(
    space_id: str = Path(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    列出空间内的所有共享文档（笔记 + 知识库文档）。

    组织成员都可查看。同时查询 SpaceDocument 表中的笔记和 VectorStoreService 中的知识库文档。

    参数:
        space_id (str): 空间 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 documents 列表和 total 数量。
        文档分为 note 类型和 knowledge 类型。

    关键逻辑:
        - 笔记通过 SpaceDocument 和 Note 表 JOIN 查询获取。
        - 知识库文档通过 VectorStoreService 获取，查询失败时返回空列表。
    """
    await _get_space_for_member(db, space_id, user_id)

    stmt = (
        select(SpaceDocument, Note)
        .join(
            Note,
            (SpaceDocument.resource_type == "note") & (SpaceDocument.resource_id == Note.id),
        )
        .where(SpaceDocument.space_id == space_id)
        .order_by(SpaceDocument.created_at.desc())
    )
    result = await db.execute(stmt)
    note_docs = [_note_document_to_dict(space_doc, note) for space_doc, note in result.all()]

    knowledge_docs = []
    try:
        store = VectorStoreService()
        for item in await store.get_user_documents(None, space_id=space_id):
            knowledge_docs.append({
                "id": item.get("id") or item.get("filename"),
                "resource_type": "knowledge",
                "resource_id": item.get("id") or item.get("filename"),
                "title": item.get("original_filename") or item.get("filename"),
                "preview": item.get("preview", ""),
                "owner_id": item.get("user_id"),
                "space_id": item.get("space_id"),
                "chunk_count": item.get("chunk_count", 0),
                "image_count": item.get("image_count", 0),
                "created_at": item.get("created_at"),
            })
    except Exception:
        knowledge_docs = []

    documents = note_docs + knowledge_docs
    return success_response(data={"documents": documents, "total": len(documents)})


@space_router.post("/{space_id}/documents/note/{note_id}")
async def add_note_to_space(
    space_id: str,
    note_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    把自己的笔记加入空间。

    加入后该组织的所有成员都能在空间内看到该笔记。只能加入自己的笔记，
    重复加入会通过唯一约束拦截。

    参数:
        space_id (str): 空间 ID。
        note_id (str): 笔记 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，data 包含 space_document_id 和 note_id。

    异常:
        HTTPException: 笔记不属于当前用户时抛出 404。
        HTTPException: 该笔记已在空间中时抛出 400（IntegrityError）。
    """
    space, _ = await _get_space_for_member(db, space_id, user_id)

    note_result = await db.execute(select(Note).where(Note.id == note_id, Note.user_id == user_id))
    note = note_result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="只能加入自己的笔记")

    space_doc = SpaceDocument(
        space_id=space_id,
        resource_type="note",
        resource_id=note_id,
        added_by=user_id,
    )
    db.add(space_doc)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="该笔记已在空间中")

    await write_audit_log(db, user_id, "add", "space_document", note_id,
                          org_id=space.org_id, detail={"space_id": space_id, "title": note.title})
    await db.commit()

    return success_response(message="笔记已加入空间", data={"space_document_id": space_doc.id, "note_id": note_id})


@space_router.delete("/{space_id}/documents/{space_document_id}")
async def remove_space_document(
    space_id: str,
    space_document_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    从空间移除共享文档。

    仅 owner/admin 或文档添加者本人可移除。

    参数:
        space_id (str): 空间 ID。
        space_document_id (str): 空间文档记录 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，message 为"文档已从空间移除"。

    异常:
        HTTPException: 空间文档不存在时抛出 404。
        HTTPException: 权限不足时抛出 403。
    """
    space, role = await _get_space_for_member(db, space_id, user_id)

    result = await db.execute(
        select(SpaceDocument).where(
            SpaceDocument.id == space_document_id,
            SpaceDocument.space_id == space_id,
        )
    )
    space_doc = result.scalar_one_or_none()
    if not space_doc:
        raise HTTPException(status_code=404, detail="空间文档不存在")
    if role not in ("owner", "admin") and space_doc.added_by != user_id:
        raise HTTPException(status_code=403, detail="权限不足")

    await db.execute(delete(SpaceDocument).where(SpaceDocument.id == space_document_id))
    await write_audit_log(db, user_id, "remove", "space_document", space_doc.resource_id,
                          org_id=space.org_id, detail={"space_id": space_id, "resource_type": space_doc.resource_type})
    await db.commit()
    return success_response(message="文档已从空间移除")


@space_router.put("/{space_id}")
async def update_space(
    space_id: str,
    payload: SpaceUpdate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    更新空间的名称和/或描述。

    仅 owner 或 admin 可执行。只更新请求中非 None 的字段。

    参数:
        space_id (str): 空间 ID。
        payload (SpaceUpdate): 更新请求体，name 和 description 均可选。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，message 为"空间更新成功"。

    异常:
        SpaceNotFoundException: 空间不存在时抛出。
        HTTPException: 权限不足时抛出 403。
        HTTPException: 空间名称为空时抛出 400。
    """
    stmt = select(Space).where(Space.id == space_id)
    result = await db.execute(stmt)
    space = result.scalar_one_or_none()
    if not space:
        raise SpaceNotFoundException()

    role = await _get_user_role(db, space.org_id, user_id)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="权限不足")

    if payload.name is not None:
        space_name = payload.name.strip()
        if not space_name:
            raise HTTPException(status_code=400, detail="空间名称不能为空")
        space.name = space_name
    if payload.description is not None:
        space.description = payload.description

    await write_audit_log(db, user_id, "update", "space", space_id,
                          org_id=space.org_id, detail={"name": space.name})
    await db.commit()

    return success_response(message="空间更新成功")


@space_router.delete("/{space_id}")
async def delete_space(
    space_id: str = Path(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    删除指定空间。

    仅 owner 或 admin 可执行。

    参数:
        space_id (str): 空间 ID。
        user_id (str): 当前登录用户 ID。
        db (AsyncSession): 异步数据库会话。

    返回:
        成功响应，message 为"空间已删除"。

    异常:
        SpaceNotFoundException: 空间不存在时抛出。
        HTTPException: 权限不足时抛出 403。
    """
    stmt = select(Space).where(Space.id == space_id)
    result = await db.execute(stmt)
    space = result.scalar_one_or_none()
    if not space:
        raise SpaceNotFoundException()

    role = await _get_user_role(db, space.org_id, user_id)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="权限不足")

    await write_audit_log(db, user_id, "delete", "space", space_id,
                          org_id=space.org_id, detail={"name": space.name})
    await db.execute(delete(Space).where(Space.id == space_id))
    await db.commit()

    return success_response(message="空间已删除")
