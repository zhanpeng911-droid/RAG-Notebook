"""
文档索引服务 —— 实现上传与索引解耦的核心逻辑。

M0 阶段关键改造：
- 上传文件时只保存文件和元数据，不强制要求 embedding 可用
- 文件状态跟踪：uploaded → parsed → pending_index → indexing → indexed / index_failed
- embedding 可用时由 Celery 后台建立向量索引
- 用户可查看索引状态并手动重试失败的索引
"""
import os
import uuid
import asyncio
from typing import Optional, List
from datetime import datetime

from fastapi import UploadFile

from app.core.logger_handler import logger
from app.utils.path_tool import get_data_path
from app.utils.file_handler import get_file_md5_hex_sync
from app.models.document_index import DocumentIndexStatus
from app.services.knowledge_file_validator import MAX_FILE_SIZE, safe_filename, validate_file_type


# 知识库文件存储根目录
KNOWLEDGE_STORAGE_DIR = "knowledge_files"


def _get_storage_dir(user_id: str) -> str:
    """获取用户知识库文件存储目录"""
    return os.path.join(get_data_path(), KNOWLEDGE_STORAGE_DIR, user_id)


# embedding 健康检查缓存 —— 避免每次上传都调用 API
# 结构: {"available": bool, "checked_at": float, "error": str}
_embedding_health_cache = {
    "available": None,
    "checked_at": 0,
    "error": "",
}

# 健康检查缓存有效期（秒）—— 5 分钟内不重复检查
_EMBEDDING_HEALTH_CACHE_TTL = 300


def _check_embedding_available(force_check: bool = False) -> bool:
    """
    检查 embedding 服务是否真正可用。

    区分三个层次：
    1. 配置可构造：模型类型、依赖包、API Key 是否存在
    2. 模型对象可构造：resolve() 是否成功
    3. 实际服务可调用：对 DashScope 做轻量 embed_query 验证

    缓存策略：
    - 检查结果缓存 5 分钟，避免每次上传都调用 API
    - force_check=True 可跳过缓存强制检查
    - 实际索引任务中以真实调用结果为准（此函数仅做预检）
    """
    import time

    global _embedding_health_cache

    now = time.time()

    # 使用缓存（除非强制检查）
    if not force_check and _embedding_health_cache["available"] is not None:
        if now - _embedding_health_cache["checked_at"] < _EMBEDDING_HEALTH_CACHE_TTL:
            return _embedding_health_cache["available"]

    try:
        from app.utils.factory import embed_model
        embed = embed_model.resolve()
        if embed is None:
            _embedding_health_cache = {"available": False, "checked_at": now, "error": "模型对象为空"}
            return False

        # 对 DashScope 嵌入做轻量连通性检查
        embed_type = type(embed).__name__
        if 'DashScope' in embed_type:
            try:
                # 用极短文本做一次 embed_query 验证 API 可达
                # 注意：这会产生极小的 API 调用成本（约 1 token）
                embed.embed_query("ping")
                _embedding_health_cache = {"available": True, "checked_at": now, "error": ""}
                logger.info("【文档索引】embedding 健康检查通过")
                return True
            except Exception as api_err:
                error_msg = str(api_err)[:200]
                _embedding_health_cache = {"available": False, "checked_at": now, "error": error_msg}
                logger.warning(f"【文档索引】DashScope embedding API 不可达: {error_msg}")
                return False

        # Ollama 等本地模型，对象存在即视为可用
        _embedding_health_cache = {"available": True, "checked_at": now, "error": ""}
        return True
    except Exception as e:
        error_msg = str(e)[:200]
        _embedding_health_cache = {"available": False, "checked_at": now, "error": error_msg}
        logger.warning(f"【文档索引】embedding 服务不可用: {error_msg}")
        return False


def get_embedding_health_status() -> dict:
    """
    获取 embedding 健康检查状态（供 API 查询）。

    返回：
        dict: {"available": bool, "error": str, "checked_at": float}
    """
    return {
        "available": _embedding_health_cache["available"],
        "error": _embedding_health_cache["error"],
        "checked_at": _embedding_health_cache["checked_at"],
    }


def validate_uploaded_content(content: bytes, filename: str) -> Optional[str]:
    """Validate a decoupled-upload payload before it is persisted."""
    type_error = validate_file_type(content, filename)
    if type_error:
        return type_error
    if len(content) > MAX_FILE_SIZE:
        return "\u6587\u4ef6\u5927\u5c0f\u4e0d\u80fd\u8d85\u8fc720MB"
    return None


async def save_uploaded_file(
    file: UploadFile,
    user_id: str,
    space_id: str = "",
) -> dict:
    """
    保存上传文件到持久化存储，并创建索引记录。

    流程：
    1. 读取文件内容
    2. 计算 MD5
    3. 保存到持久化目录
    4. 在 MySQL 创建 document_index 记录
    5. 根据 embedding 可用性决定是否提交后台索引任务

    返回：
        dict: 包含 document_id, filename, status 等信息
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    # 读取文件内容
    content = await file.read()
    await file.seek(0)

    filename = safe_filename(file)
    validation_error = validate_uploaded_content(content, filename)
    if validation_error:
        raise ValueError(validation_error)

    file_size = len(content)
    file_type = os.path.splitext(filename)[1].lower()

    # 保存到临时文件计算 MD5
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_type) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        md5_hex = get_file_md5_hex_sync(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # 创建持久化存储目录
    storage_dir = _get_storage_dir(user_id)
    os.makedirs(storage_dir, exist_ok=True)

    # 生成唯一文件名（避免冲突）
    unique_filename = f"{uuid.uuid4().hex[:12]}_{filename}"
    file_path = os.path.join(storage_dir, unique_filename)

    # 保存文件
    with open(file_path, 'wb') as f:
        f.write(content)

    logger.info(f"【文档索引】文件已保存: {file_path}, size={file_size}")

    # 创建 MySQL 记录
    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        # 文件名唯一性校验（按用户隔离，同名拒绝）
        existing_filename = await repo.get_by_original_filename(filename, user_id)
        if existing_filename:
            # 同名文件已存在，删除刚保存的物理文件，拒绝上传
            if os.path.exists(file_path):
                os.unlink(file_path)
            return {
                "document_id": existing_filename.id,
                "filename": existing_filename.original_filename,
                "status": existing_filename.status.value,
                "message": f"文件名「{filename}」已存在，请重命名后重新上传",
                "duplicate_filename": True,
            }

        # 检查是否已存在（MD5 内容去重）
        existing = await repo.get_by_md5(md5_hex, user_id)
        if existing:
            # 内容相同的文件已存在，删除刚保存的文件
            if os.path.exists(file_path):
                os.unlink(file_path)
            return {
                "document_id": existing.id,
                "filename": existing.original_filename,
                "status": existing.status.value,
                "message": "文件内容已存在",
                "duplicate": True,
            }

        # 创建新记录
        doc = await repo.create(
            user_id=user_id,
            filename=unique_filename,
            original_filename=filename,
            file_path=file_path,
            md5=md5_hex,
            file_size=file_size,
            file_type=file_type,
            space_id=space_id,
            status=DocumentIndexStatus.UPLOADED,
        )

        # 更新为 parsed 状态（文件已保存，文本可解析）
        await repo.update_status(doc.id, DocumentIndexStatus.PARSED)
        await repo.update_status(doc.id, DocumentIndexStatus.PENDING_INDEX)
        await session.commit()

        # 上传时立即尝试同步索引 —— embedding 可用就当场完成
        try:
            await _sync_index(doc.id, user_id)
            logger.info(f"【文档索引】上传时同步索引完成: id={doc.id}")
            return {
                "document_id": doc.id,
                "filename": filename,
                "status": "indexed",
                "message": "文件上传成功，已完成索引",
                "duplicate": False,
            }
        except Exception as e:
            error_msg = str(e)[:200]
            logger.warning(f"【文档索引】上传时索引失败，标记为 pending: id={doc.id}, error={error_msg}")
            # 索引失败，提交 Celery 后台重试
            try:
                from app.tasks.index_task import index_document_task
                index_document_task.delay(doc.id, user_id)
            except Exception:
                pass
            return {
                "document_id": doc.id,
                "filename": filename,
                "status": "pending_index",
                "message": f"文件已保存，索引失败: {error_msg}",
                "duplicate": False,
            }


async def _sync_index(document_id: str, user_id: str):
    """
    同步索引（当 Celery 不可用时的降级方案）。

    在当前请求中直接执行索引，而不是交给后台任务。
    创建独立的数据库会话，避免与调用者的会话状态冲突。
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        try:
            await repo.update_status(document_id, DocumentIndexStatus.INDEXING)
            await session.commit()

            doc = await repo.get_by_id(document_id, user_id)
            if not doc:
                return

            from app.rag.vector_store import VectorStoreService
            store = VectorStoreService()

            # 加载文档
            documents = await store.get_file_document(
                doc.file_path, md5=doc.md5, user_id=user_id
            )
            if not documents:
                await repo.update_status(
                    document_id,
                    DocumentIndexStatus.INDEX_FAILED,
                    error_message="文档加载为空"
                )
                await session.commit()
                return

            # 注入元数据
            for d in documents:
                d.metadata['user_id'] = user_id
                d.metadata['original_filename'] = doc.original_filename
                d.metadata['md5'] = doc.md5
                d.metadata['space_id'] = doc.space_id or ''

            # 切片
            split_docs = store.split_documents_sync(documents)
            if not split_docs:
                await repo.update_status(
                    document_id,
                    DocumentIndexStatus.INDEX_FAILED,
                    error_message="文档切片为空"
                )
                await session.commit()
                return

            # 写入向量数据库
            await asyncio.to_thread(store.vectors_store.add_documents, split_docs)

            # 保存 MD5 记录
            await store.save_md5_hex(doc.md5, doc.filename, doc.original_filename, user_id)

            # 更新状态为 indexed
            await repo.update_status(
                document_id,
                DocumentIndexStatus.INDEXED,
                chunk_count=len(split_docs)
            )
            await session.commit()

            logger.info(f"【文档索引】同步索引完成: id={document_id}, chunks={len(split_docs)}")

        except Exception as e:
            logger.error(f"【文档索引】同步索引失败: id={document_id}, error={e}")
            try:
                await repo.update_status(
                    document_id,
                    DocumentIndexStatus.INDEX_FAILED,
                    error_message=str(e)[:500]
                )
                await session.commit()
            except Exception:
                pass
            raise


async def reindex_document(document_id: str, user_id: str) -> dict:
    """
    重新索引文档（用于索引失败后的重试）。

    不要求 embedding 预检可用——由实际索引任务决定成功或失败。
    这样即使 embedding 服务暂时不可用，用户也可以先提交任务，
    等服务恢复后由 Celery Beat 自动补偿。

    返回：
        dict: 操作结果
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        doc = await repo.get_by_id(document_id, user_id)
        if not doc:
            return {"success": False, "message": "文档不存在"}

        if doc.status not in [DocumentIndexStatus.INDEX_FAILED, DocumentIndexStatus.PENDING_INDEX]:
            return {"success": False, "message": f"当前状态 {doc.status.value} 不允许重新索引"}

        # 检查文件是否存在
        if not os.path.exists(doc.file_path):
            return {"success": False, "message": "文件已丢失，无法重新索引"}

        # 更新状态为 pending_index（无论 embedding 是否可用）
        await repo.update_status(document_id, DocumentIndexStatus.PENDING_INDEX)
        await repo.increment_retry(document_id)
        await session.commit()

        # 提交 Celery 任务
        from app.tasks.index_task import index_document_task
        try:
            index_document_task.delay(document_id, user_id)
            return {"success": True, "message": "已提交重新索引任务"}
        except Exception as e:
            logger.warning(f"【文档索引】提交 Celery 任务失败: {e}")
            # 降级为同步索引
            try:
                await _sync_index(document_id, user_id)
                return {"success": True, "message": "已同步完成重新索引"}
            except Exception as sync_err:
                logger.error(f"【文档索引】同步索引也失败: {sync_err}")
                return {"success": False, "message": f"索引失败: {str(sync_err)[:200]}"}


async def get_user_index_status(user_id: str, space_id: str = None) -> List[dict]:
    """
    获取用户的文档索引状态列表。

    返回：
        List[dict]: 文档索引状态列表
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)
        docs = await repo.get_user_documents(user_id, space_id=space_id)

        return [
            {
                "id": doc.id,
                "filename": doc.original_filename,
                "file_type": doc.file_type,
                "file_size": doc.file_size,
                "md5": doc.md5,
                "status": doc.status.value,
                "chunk_count": doc.chunk_count,
                "error_message": doc.error_message,
                "retry_count": doc.retry_count,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
                "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
            }
            for doc in docs
        ]


async def delete_index_record(document_id: str, user_id: str, delete_file: bool = True) -> dict:
    """
    删除文档索引记录（v2 路径）。

    删除顺序（保证一致性）：
    1. 查询 DocumentIndex（按 document_id + user_id）
    2. 如有向量，按 user_id + md5 删除 Chroma 向量和旧 MD5 记录
    3. 安全删除 knowledge_files 下的物理文件（校验 realpath 必须在用户目录内）
    4. 删除 document_index 记录并显式 commit

    幂等：文档不存在也返回成功。
    支持所有状态：pending_index / indexing / index_failed / indexed

    返回：
        dict: {"success": bool, "message": str}
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        doc = await repo.get_by_id(document_id, user_id)
        if not doc:
            return {"success": True, "message": "文档不存在或已删除（幂等）"}

        saved_md5 = doc.md5
        saved_status = doc.status

        # 如果文档已索引或正在索引，清理向量和旧 MD5 记录
        if saved_status in (DocumentIndexStatus.INDEXED, DocumentIndexStatus.INDEXING) and saved_md5:
            try:
                from app.rag.vector_store import VectorStoreService
                store = VectorStoreService()
                where_clause = {"$and": [{"user_id": user_id}, {"md5": saved_md5}]}
                await asyncio.to_thread(store.vectors_store.delete, where=where_clause)
                logger.info(f"【文档索引】已删除向量: user_id={user_id}, md5={saved_md5}")
            except Exception as e:
                logger.warning(f"【文档索引】删除向量失败（可能未索引）: {e}")

            try:
                from app.services.knowledge_record_service import KnowledgeRecordService
                record_service = KnowledgeRecordService()
                await record_service.delete_single_md5(user_id, saved_md5, delete_documents=False)
            except Exception as e:
                logger.warning(f"【文档索引】删除 MD5 记录失败: {e}")

        # 安全删除物理文件
        if delete_file and doc.file_path:
            _safe_delete_physical_file(doc.file_path, user_id)

        # 删除 document_index 记录
        await repo.delete_by_id(document_id, user_id)
        await session.commit()
        logger.info(f"【文档索引】已删除记录: id={document_id}")

        return {"success": True, "message": "文档已删除"}


def _safe_delete_physical_file(file_path: str, user_id: str) -> None:
    """安全删除物理文件，校验 realpath 必须在用户目录内"""
    if not file_path or not os.path.exists(file_path):
        return

    try:
        user_dir = _get_storage_dir(user_id)
        real_file = os.path.realpath(file_path)
        real_user_dir = os.path.realpath(user_dir)

        if not real_file.startswith(real_user_dir):
            logger.warning(f"【文档索引】文件路径不在用户目录内，拒绝删除: {file_path}")
            return

        os.unlink(file_path)
        logger.info(f"【文档索引】已删除物理文件: {file_path}")
    except Exception as e:
        logger.warning(f"【文档索引】删除物理文件失败: {e}")


async def clean_user_index_records(user_id: str, space_id: str = None) -> dict:
    """
    清除用户的所有 v2 文档索引记录、向量和物理文件。

    参数：
        user_id: 用户ID
        space_id: 空间ID（None 表示清理所有）

    返回：
        dict: {"success": bool, "deleted_count": int, "message": str}
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)
        docs = await repo.get_user_documents(user_id, space_id=space_id)

        if not docs:
            return {"success": True, "deleted_count": 0, "message": "没有需要清除的记录"}

        # 复用同一个 VectorStoreService 实例
        store = None
        try:
            from app.rag.vector_store import VectorStoreService
            store = VectorStoreService()
        except Exception as e:
            logger.warning(f"【文档索引】VectorStoreService 初始化失败（可能 Chroma 不可用）: {e}")

        deleted_count = 0
        for doc in docs:
            # 如果已索引或正在索引，清理向量
            if doc.status in (DocumentIndexStatus.INDEXED, DocumentIndexStatus.INDEXING) and doc.md5:
                if store:
                    try:
                        where_clause = {"$and": [{"user_id": user_id}, {"md5": doc.md5}]}
                        await asyncio.to_thread(store.vectors_store.delete, where=where_clause)
                    except Exception as e:
                        logger.warning(f"【文档索引】清除向量失败: {e}")

                try:
                    from app.services.knowledge_record_service import KnowledgeRecordService
                    record_service = KnowledgeRecordService()
                    await record_service.delete_single_md5(user_id, doc.md5, delete_documents=False)
                except Exception as e:
                    logger.warning(f"【文档索引】清除 MD5 记录失败: {e}")

            # 删除物理文件
            _safe_delete_physical_file(doc.file_path, user_id)

            # 删除记录
            await repo.delete_by_id(doc.id, user_id)
            deleted_count += 1

        await session.commit()
        logger.info(f"【文档索引】已清除用户 {user_id} 的 {deleted_count} 条 v2 记录")

        return {"success": True, "deleted_count": deleted_count, "message": f"已清除 {deleted_count} 条记录"}
