"""
知识库路由 —— 处理文档上传、管理、检索的 API 接口。

接口列表：
- POST /knowledge/add/single         —— 上传单个文件
- POST /knowledge/add/multiple       —— 上传多个文件
- POST /knowledge/add/multiple/stream —— 流式上传（实时进度）
- DELETE /knowledge/clean             —— 清空用户向量
- DELETE /knowledge/md5/clear         —— 清空 MD5 记录
- DELETE /knowledge/md5/delete/{md5}  —— 删除单个 MD5 记录
- DELETE /knowledge/delete/filename   —— 按文件名删除
- GET  /knowledge/md5/list            —— 获取 MD5 记录列表
- GET  /knowledge/md5/{md5}           —— 获取 MD5 详情
- GET  /knowledge/list                —— 获取知识库文档列表
- GET  /knowledge/detail              —— 获取文档详情
- GET  /knowledge/chunks              —— 获取文档切片
- GET  /knowledge/image/{md5}/{filename} —— 获取 PDF 图片
- GET  /knowledge/images/all/{md5}    —— 批量获取图片
"""
import os
from typing import List, Optional

from fastapi.routing import APIRouter
from fastapi import UploadFile, File, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.router.knowledge_service import KnowledgeService, get_knowledge_service

from app.schemas.models import MD5Record, MD5ListResponse, KnowledgeListResponse, KnowledgeDocumentDetail, DocumentChunksResponse
from app.utils.auth_utils import get_current_user_id
from app.db.db_config import get_db
from app.models.space import Space
from app.core.permission import _get_user_role
from app.utils.image_extractor import get_image_storage_dir
from app.utils.path_tool import get_data_path
from app.core.success_response import success_response
from app.core.rate_limit import rate_limit
from app.core.logger_handler import logger


knowledge_router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def _ensure_space_member(space_id: Optional[str], user_id: str, db: AsyncSession) -> Optional[Space]:
    """Validate that a non-empty space_id exists and belongs to an org the user can access."""
    if not space_id:
        return None

    result = await db.execute(select(Space).where(Space.id == space_id))
    space = result.scalar_one_or_none()
    if not space:
        raise HTTPException(status_code=404, detail="空间不存在")

    role = await _get_user_role(db, space.org_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="您不是该空间所属组织的成员")

    return space


@knowledge_router.post("/add/single")
async def add_vector_single(
        file: UploadFile = File(...),
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        space_id: Optional[str] = Query(None, description="归属空间ID"),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=5, window=60))
):
    """上传文件，将文件保存到向量数据库，仅支持TXT和PDF"""
    await _ensure_space_member(space_id, user_id, db)
    filename = await knowledge_service.handle_add_vector_single(file, user_id, space_id=space_id or "")
    return success_response(message=f"文件 {filename} 已成功上传并存储到向量数据库")


@knowledge_router.post("/add/multiple")
async def add_vector_multiple(
        files: List[UploadFile] = File(..., description="要上传的文件列表，仅支持PDF和TXT格式"),
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        space_id: Optional[str] = Query(None, description="归属空间ID"),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=3, window=60))
):
    """上传多个文件，将文件保存到向量数据库，仅支持TXT和PDF"""
    await _ensure_space_member(space_id, user_id, db)
    filenames = await knowledge_service.handle_add_vector_multiple(files, user_id, space_id=space_id or "")
    return success_response(message=f"文件 {filenames} 已成功上传并存储到向量数据库")


@knowledge_router.post("/add/multiple/stream")
async def add_vector_multiple_stream(
        files: List[UploadFile] = File(..., description="要上传的文件列表，仅支持PDF、TXT、MD、PPTX、DOCX格式"),
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        space_id: Optional[str] = Query(None, description="归属空间ID"),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=3, window=60))
):
    """上传多个文件，流式返回处理进度，仅支持TXT、PDF、MD、PPTX、DOCX"""
    await _ensure_space_member(space_id, user_id, db)
    return StreamingResponse(
        knowledge_service.handle_add_vector_multiple_stream(files, user_id, space_id=space_id or ""),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*"
        }
    )


@knowledge_router.delete("/clean")
async def clean_user_vectors(
    user_id: str = Depends(get_current_user_id),
    space_id: Optional[str] = Query(None, description="按空间清理"),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
):
    """
    清除用户上传的所有向量（同时清理 v1 旧路径和 v2 document_index）。

    - Chroma 向量
    - 旧 MD5 记录
    - document_index 记录
    - knowledge_files 下的物理文件
    """
    # 带空间参数时必须只清理该空间，不能调用按用户全量删除的旧 v1 路径。
    if space_id:
        from app.services.document_index_service import clean_user_index_records
        result = await clean_user_index_records(user_id, space_id=space_id)
        return success_response(
            message=f"已成功清除空间内文档（v2 删除 {result['deleted_count']} 条记录）"
        )

    # 未指定空间时才执行用户级全量清理。
    await knowledge_service.clean_user_upload(user_id)

    from app.services.document_index_service import clean_user_index_records
    result = await clean_user_index_records(user_id)

    return success_response(
        message=f"已成功清除用户上传的所有向量（v2 删除 {result['deleted_count']} 条记录）"
    )


@knowledge_router.delete("/md5/clear")
async def clear_user_md5(
        delete_documents: bool = True,
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service)
):
    """
    清空用户的MD5记录
    :param delete_documents: 是否同时删除知识库文档（默认True）
    """
    await knowledge_service.handle_clear_user_md5(user_id, delete_documents)
    if delete_documents:
        return success_response(message="已成功清空用户的MD5记录和知识库文档")
    else:
        return success_response(message="已成功清空用户的MD5记录（保留知识库文档）")


@knowledge_router.delete("/md5/delete/{md5_value}")
async def delete_single_md5(
        md5_value: str,
        delete_documents: bool = True,
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service)
):
    """
    删除单个MD5记录及其对应的知识库内容
    :param md5_value: 要删除的MD5值
    :param delete_documents: 是否同时删除知识库文档（默认True）
    """
    success = await knowledge_service.handle_delete_single_md5(user_id, md5_value, delete_documents)
    if success:
        if delete_documents:
            return success_response(message=f"已成功删除MD5记录 {md5_value} 及其对应的知识库文档")
        else:
            return success_response(message=f"已成功删除MD5记录 {md5_value}（保留知识库文档）")
    else:
        raise HTTPException(status_code=404, detail=f"MD5记录 {md5_value} 不存在")


@knowledge_router.delete("/delete/filename")
async def delete_by_filename(
        filename: str,
        delete_documents: bool = True,
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service)
):
    """
    通过文件名删除文档（兼容 v1 旧路径和 v2 document_index）。

    删除策略：
    1. 先从 document_index 表查找匹配的 v2 文档
    2. 如果找到，使用 v2 删除逻辑（清理向量、物理文件、document_index 记录）
    3. 如果没找到 v2 记录，回退到旧 MD5 删除逻辑
    """
    from app.services.document_index_service import delete_index_record

    # 先尝试 v2 路径：从 document_index 表查找
    deleted_via_v2 = False
    try:
        from app.db.db_config import AsyncSessionLocal
        from app.repositories.document_index_repository import DocumentIndexRepository

        async with AsyncSessionLocal() as session:
            repo = DocumentIndexRepository(session)
            # 按 original_filename 查找 v2 记录
            docs = await repo.get_user_documents(user_id)
            for doc in docs:
                if doc.original_filename == filename:
                    result = await delete_index_record(doc.id, user_id, delete_file=delete_documents)
                    if result["success"]:
                        deleted_via_v2 = True
                        break
    except Exception as e:
        logger.warning(f"【删除】v2 路径查找失败，回退到 v1: {e}")

    if deleted_via_v2:
        return success_response(message=f"已成功删除文件 {filename}")

    # 回退到 v1 旧路径
    success = await knowledge_service.handle_delete_by_filename(user_id, filename, delete_documents)
    if success:
        if delete_documents:
            return success_response(message=f"已成功删除文件 {filename} 的MD5记录及其对应的知识库文档")
        else:
            return success_response(message=f"已成功删除文件 {filename} 的MD5记录（保留知识库文档）")
    else:
        raise HTTPException(status_code=404, detail=f"文件 {filename} 不存在")


@knowledge_router.get("/md5/list", response_model=MD5ListResponse)
async def get_all_md5_records(
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """获取用户的所有MD5记录"""
    records = await knowledge_service.handle_get_all_md5_records(user_id)
    return success_response(data=MD5ListResponse(
        records=records,
        total_count=len(records)
    ))


@knowledge_router.get("/md5/{md5_value}", response_model=MD5Record)
async def get_md5_info(
        md5_value: str,
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """
    获取MD5对应的文档信息
    :param md5_value: MD5值
    """
    md5_info = await knowledge_service.handle_get_md5_info(user_id, md5_value)
    if md5_info:
        return success_response(data=md5_info)
    else:
        raise HTTPException(status_code=404, detail=f"MD5记录 {md5_value} 不存在")


@knowledge_router.get("/list", response_model=KnowledgeListResponse)
async def get_user_knowledge_list(
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        space_id: Optional[str] = Query(None, description="按空间筛选"),
        include_index_status: bool = Query(False, description="是否包含索引状态信息"),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """获取用户的知识库文档列表（可按空间筛选）"""
    await _ensure_space_member(space_id, user_id, db)
    documents_user_id = None if space_id else user_id
    documents = await knowledge_service.handle_get_user_knowledge(documents_user_id, space_id=space_id or None)

    # 如果需要包含索引状态，合并 document_index 表的数据
    if include_index_status:
        from app.services.document_index_service import get_user_index_status
        index_records = await get_user_index_status(user_id, space_id=space_id)
        index_map = {r["md5"]: r for r in index_records}

        for doc in documents:
            doc_md5 = doc.get("md5") or ""
            if doc_md5 in index_map:
                doc["index_status"] = index_map[doc_md5]["status"]
                doc["index_error"] = index_map[doc_md5].get("error_message")
                doc["indexed_at"] = index_map[doc_md5].get("indexed_at")

    return success_response(data=KnowledgeListResponse(
        documents=documents,
        total_count=len(documents)
    ))


@knowledge_router.get("/detail", response_model=KnowledgeDocumentDetail)
async def get_document_detail(
        filename: str,
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """获取文档详情内容"""
    document = await knowledge_service.handle_get_document_detail(user_id, filename)
    return success_response(data=document)


@knowledge_router.get("/chunks", response_model=DocumentChunksResponse)
async def get_document_chunks(
        filename: str,
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """获取文档切片信息"""
    chunks = await knowledge_service.handle_get_document_chunks(user_id, filename)
    return success_response(data=chunks)


@knowledge_router.get("/debug/metadata")
async def debug_metadata(
        user_id: str = Depends(get_current_user_id),
):
    """调试端点：查看 ChromaDB 中所有文档的 metadata"""
    import asyncio
    from app.rag.vector_store import VectorStoreService
    store = VectorStoreService()
    all_docs = await asyncio.to_thread(
        store.vectors_store.get,
        include=['metadatas'],
        where={"user_id": user_id}
    )
    result = []
    for i, doc_id in enumerate(all_docs['ids'][:10]):
        meta = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
        result.append({
            "id": doc_id,
            "metadata": meta
        })
    return {"user_id": user_id, "total": len(all_docs['ids']), "sample": result}


# 图片服务端点：提供 PDF 中提取的原始图片的访问入口。
# 图片本身存储在服务器文件系统中，不直接对外暴露路径，而是通过此 API 做鉴权后返回。
# 这对安全性很重要——用户必须持有有效 JWT token 才能访问自己的图片。
@knowledge_router.get("/image/{md5}/{filename}")
async def serve_knowledge_image(
        md5: str,
        filename: str,
        user_id: str = Depends(get_current_user_id),
):
    """
    返回PDF中提取的原始图片（需JWT鉴权）
    图片存储在 data/extracted_images/{user_id}/{md5}/{filename}
    """
    # 防止路径穿越：只取文件名部分，去掉任何目录前缀
    safe_filename = os.path.basename(filename)
    if safe_filename != filename or '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    image_dir = get_image_storage_dir(user_id, md5)
    image_path = os.path.join(image_dir, safe_filename)

    # 验证解析后的路径仍在预期目录内
    real_image_dir = os.path.realpath(image_dir)
    real_image_path = os.path.realpath(image_path)
    if not real_image_path.startswith(real_image_dir):
        raise HTTPException(status_code=400, detail="非法路径")

    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="图片不存在")

    # 根据文件扩展名设置正确的 Content-Type，确保浏览器正确渲染图片
    ext = os.path.splitext(filename)[1].lower()
    media_type_map = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.tiff': 'image/tiff',
        '.tif': 'image/tiff',
        '.bmp': 'image/bmp',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
    }
    media_type = media_type_map.get(ext, 'application/octet-stream')
    return FileResponse(image_path, media_type=media_type)


# 批量图片获取接口：一次性拿到某个文档的所有图片，前端缓存后按需展示。
# 使用 base64 编码嵌入 JSON 中，减少前端的 HTTP 请求次数（尤其适合移动端）。
@knowledge_router.get("/images/all/{md5}")
async def serve_batch_images(
        md5: str,
        user_id: str = Depends(get_current_user_id),
        knowledge_service: KnowledgeService = Depends(get_knowledge_service),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """返回指定PDF的所有图片（单次请求，JSON + base64）"""
    result = await knowledge_service.handle_get_batch_images(user_id, md5)
    return success_response(data=result)


# ==================== M0: 文档索引状态管理 ====================

@knowledge_router.get("/index-status")
async def get_index_status(
        user_id: str = Depends(get_current_user_id),
        space_id: Optional[str] = Query(None, description="按空间筛选"),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """获取用户的文档索引状态列表"""
    from app.services.document_index_service import get_user_index_status
    await _ensure_space_member(space_id, user_id, db)
    records = await get_user_index_status(user_id, space_id=space_id)
    return success_response(data={"documents": records, "total_count": len(records)})


@knowledge_router.get("/embedding-health")
async def get_embedding_health(
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=5, window=60))
):
    """
    获取 embedding 服务健康状态。

    返回：
        - available: 是否可用
        - error: 错误信息（如果有）
        - checked_at: 上次检查时间戳
    """
    from app.services.document_index_service import get_embedding_health_status
    status = get_embedding_health_status()
    return success_response(data=status)


@knowledge_router.post("/embedding-test")
async def test_embedding(
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=3, window=60))
):
    """
    测试 embedding 服务连通性。

    执行一次真实的 embed_query 调用，验证：
    1. 模型能否正常创建
    2. API Key 是否有效
    3. 网络是否可达
    4. 模型名称是否正确

    返回：
        - success: 是否成功
        - model_type: 模型类型（ALIYUN/OLLAMA）
        - model_name: 模型名称
        - vector_dim: 返回向量维度（成功时）
        - error: 错误信息（失败时）
    """
    try:
        from app.utils.factory import embed_model
        from app.config.validator import get_settings
        settings = get_settings()

        embed = embed_model.resolve()
        if embed is None:
            return success_response(data={
                "success": False,
                "model_type": settings.EMBED_MODEL_TYPE,
                "error": "模型对象为空，检查配置"
            })

        model_type = type(embed).__name__
        model_name = getattr(embed, 'model_name', 'unknown')

        # 执行真实调用
        import time
        start = time.time()
        vector = embed.embed_query("测试连接")
        elapsed = time.time() - start

        return success_response(data={
            "success": True,
            "model_type": settings.EMBED_MODEL_TYPE,
            "model_name": model_name,
            "vector_dim": len(vector),
            "latency_ms": round(elapsed * 1000),
        })

    except Exception as e:
        return success_response(data={
            "success": False,
            "model_type": settings.EMBED_MODEL_TYPE if 'settings' in dir() else "unknown",
            "error": str(e)[:500],
        })


@knowledge_router.post("/{document_id}/reindex")
async def reindex_document(
        document_id: str,
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=5, window=60))
):
    """重新索引文档（用于索引失败后的重试）"""
    from app.services.document_index_service import reindex_document as _reindex
    result = await _reindex(document_id, user_id)
    if result["success"]:
        return success_response(message=result["message"])
    else:
        raise HTTPException(status_code=400, detail=result["message"])


@knowledge_router.delete("/documents/{document_id}")
async def delete_document_by_id(
        document_id: str,
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """
    v2 删除单个文档（按 document_id）。

    删除顺序和一致性：
    1. 查询 DocumentIndex（按 document_id + user_id）
    2. 如有向量，按 user_id + md5 删除 Chroma 向量和旧 MD5 记录
    3. 安全删除 knowledge_files 下的物理文件（校验 realpath 必须在用户目录内）
    4. 删除 document_index 记录并显式 commit
    5. 返回结构化结果

    幂等：文档不存在也返回成功。
    """
    from app.services.document_index_service import delete_index_record
    result = await delete_index_record(document_id, user_id, delete_file=True)
    if result["success"]:
        return success_response(message=result["message"])
    else:
        raise HTTPException(status_code=500, detail=result["message"])


@knowledge_router.post("/add/single/v2")
async def add_vector_single_v2(
        file: UploadFile = File(...),
        user_id: str = Depends(get_current_user_id),
        space_id: Optional[str] = Query(None, description="归属空间ID"),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=5, window=60))
):
    """
    上传单个文件（v2 解耦版本）。

    与 /add/single 的区别：
    - 上传和索引解耦，文件保存后立即返回
    - 索引在后台异步进行（如果 embedding 可用）
    - embedding 不可用时文件仍可保存，状态为 pending_index
    - 用户可通过 /index-status 查看索引进度
    """
    from app.services.document_index_service import save_uploaded_file
    await _ensure_space_member(space_id, user_id, db)
    result = await save_uploaded_file(file, user_id, space_id=space_id or "")
    if result.get("duplicate_filename"):
        raise HTTPException(status_code=409, detail=result["message"])
    if result.get("duplicate"):
        return success_response(message=f"文件 {result['filename']} 内容已存在")
    return success_response(
        data=result,
        message=result.get("message", "文件上传成功")
    )


@knowledge_router.post("/add/multiple/v2")
async def add_vector_multiple_v2(
        files: List[UploadFile] = File(..., description="要上传的文件列表"),
        user_id: str = Depends(get_current_user_id),
        space_id: Optional[str] = Query(None, description="归属空间ID"),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=3, window=60))
):
    """
    上传多个文件（v2 解耦版本）。

    逐个处理文件，每个文件独立保存和索引。
    """
    from app.services.document_index_service import save_uploaded_file
    from app.services.knowledge_file_validator import validate_total_size

    await _ensure_space_member(space_id, user_id, db)

    # 验证总大小
    total_size = sum(f.size or 0 for f in files)
    size_error = validate_total_size(total_size)
    if size_error:
        raise HTTPException(status_code=400, detail=size_error)

    results = []
    for file in files:
        try:
            result = await save_uploaded_file(file, user_id, space_id=space_id or "")
            results.append(result)
        except Exception as e:
            logger.error(f"【上传v2】处理文件 {file.filename} 失败: {e}")
            results.append({
                "filename": file.filename,
                "status": "error",
                "message": str(e),
            })

    return success_response(
        data={"results": results, "total_count": len(results)},
        message=f"已处理 {len(results)} 个文件"
    )
