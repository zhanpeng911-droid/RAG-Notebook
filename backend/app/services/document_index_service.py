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

from fastapi import UploadFile, HTTPException

from app.core.logger_handler import logger
from app.utils.path_tool import get_data_path
from app.utils.file_handler import get_file_md5_hex_sync
from app.models.document_index import DocumentIndexStatus
from app.services.knowledge_file_validator import MAX_FILE_SIZE, safe_filename, validate_file_type


# 知识库文件存储根目录
KNOWLEDGE_STORAGE_DIR = "knowledge_files"


def _sanitize_storage_filename(filename: str) -> str:
    """剥离路径成分，仅保留纯文件名（防止上传文件名路径穿越写盘）。"""
    cleaned = os.path.basename(str(filename or "").replace(chr(92), "/")).strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("非法文件名")
    return cleaned


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

    # R3 有界读取：先按声明大小预检，再分块读取并强制硬上限，
    # 避免超大请求把整个文件一次性读进内存
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > MAX_FILE_SIZE:
        raise ValueError("文件大小不能超过20MB")

    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_SIZE:
            raise ValueError("文件大小不能超过20MB")
        chunks.append(chunk)
    content = b"".join(chunks)
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
    unique_filename = f"{uuid.uuid4().hex[:12]}_{_sanitize_storage_filename(filename)}"
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
            # needs_reupload 接管（N1 闭环）：index_failed 且原文件缺失/标记需重传
            # 的行允许原子接管（文件身份与状态同事务条件更新；deleting/新执行
            # 不命中即不接管不索引）
            if existing_filename.status == DocumentIndexStatus.INDEX_FAILED                     and not _row_has_file(existing_filename):
                # A-05：存在未关闭写入者（writing/unknown）时不得自动接管——
                # 必须先经受控核验/恢复，不能凭缺文件推断远端停止
                if await _ExecutionRepository(session).has_open_executions(
                        existing_filename.id):
                    await session.commit()
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    raise HTTPException(
                        status_code=409,
                        detail="存在未核验的写入执行，请先经运维核验后再重传")
                taken = await repo.takeover_upload_row(
                    doc_id=existing_filename.id, user_id=user_id,
                    filename=unique_filename, file_path=file_path, md5=md5_hex,
                    file_size=file_size, file_type=file_type,
                    expected_generation=existing_filename.generation or 0,
                    expected_file_path=existing_filename.file_path)
                await session.commit()
                if not taken:
                    # 接管未命中：删除获胜或新执行已推进，不继续索引
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    logger.warning(
                        f"【文档索引】接管被拒（状态已变化）: id={existing_filename.id}"
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="文档正在删除或已被重新处理，请刷新后重试")
                logger.info(
                    f"【文档索引】needs_reupload 接管重传: id={existing_filename.id}, "
                    f"filename={filename}"
                )
                # N1 闭环：接管后立即走统一索引执行（新代次，仅领取一次）
                from app.models.document_index import DocumentIndexStatus as _DIS
                exec_res = await execute_index_document(existing_filename.id, user_id)
                takeover_status = exec_res["status"]
                return {
                    "document_id": existing_filename.id,
                    "filename": filename,
                    "status": takeover_status.value,
                    "message": ("已接管重传并完成索引" if takeover_status == _DIS.INDEXED
                                else "已接管重传，索引处理中"),
                    "duplicate_filename": False,
                    "takeover": True,
                }
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

        # 检查是否已存在（MD5 内容去重）——同 MD5 不同文件名同样支持 needs_reupload 接管
        existing = await repo.get_by_md5(md5_hex, user_id)
        if existing:
            if existing.status == DocumentIndexStatus.INDEX_FAILED                     and not _row_has_file(existing):
                if await _ExecutionRepository(session).has_open_executions(existing.id):
                    await session.commit()
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    raise HTTPException(
                        status_code=409,
                        detail="存在未核验的写入执行，请先经运维核验后再重传")
                taken = await repo.takeover_upload_row(
                    doc_id=existing.id, user_id=user_id,
                    filename=unique_filename, file_path=file_path, md5=md5_hex,
                    file_size=file_size, file_type=file_type,
                    expected_generation=existing.generation or 0,
                    expected_file_path=existing.file_path)
                await session.commit()
                if not taken:
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    raise HTTPException(
                        status_code=409,
                        detail="文档正在删除或已被重新处理，请刷新后重试")
                logger.info(
                    f"【文档索引】needs_reupload MD5 接管重传: id={existing.id}"
                )
                exec_res = await execute_index_document(existing.id, user_id)
                return {
                    "document_id": existing.id,
                    "filename": filename,
                    "status": exec_res["status"].value,
                    "message": ("已接管重传并完成索引"
                                if exec_res["status"] == DocumentIndexStatus.INDEXED
                                else "已接管重传，索引处理中"),
                    "duplicate": False,
                    "takeover": True,
                }
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

        # 上传时立即尝试同步索引 —— embedding 可用就当场完成。
        # 统一执行服务（R2C）返回落库状态与是否可重试：禁止把"没有抛异常"当作索引成功。
        retry_scheduled = False
        try:
            exec_result = await execute_index_document(doc.id, user_id)
        except Exception as e:
            # 防御性兜底：执行器内部已兜住业务异常；这里只承接基础设施级异常
            error_msg = str(e)[:200]
            logger.warning(f"【文档索引】上传时同步索引执行异常，落库 index_failed: id={doc.id}, error={error_msg}")
            try:
                from app.tasks.index_task import index_document_task
                index_document_task.delay(doc.id, user_id)
                retry_scheduled = True
            except Exception:
                retry_scheduled = False
            return {
                "document_id": doc.id,
                "filename": filename,
                "status": DocumentIndexStatus.INDEX_FAILED.value,
                "message": "文件已保存，但索引失败" + ("，已提交后台重试" if retry_scheduled else ""),
                "retry_scheduled": retry_scheduled,
                "duplicate": False,
            }

        sync_status = exec_result["status"]

        if sync_status == DocumentIndexStatus.INDEXED:
            logger.info(f"【文档索引】上传时同步索引完成: id={doc.id}")
            return {
                "document_id": doc.id,
                "filename": filename,
                "status": "indexed",
                "message": "文件上传成功，已完成索引",
                "duplicate": False,
            }

        if sync_status == DocumentIndexStatus.INDEX_FAILED:
            if exec_result.get("retryable"):
                # 可重试失败（如 embedding 暂不可用）：提交后台重试
                try:
                    from app.tasks.index_task import index_document_task
                    index_document_task.delay(doc.id, user_id)
                    retry_scheduled = True
                except Exception:
                    retry_scheduled = False
            return {
                "document_id": doc.id,
                "filename": filename,
                "status": DocumentIndexStatus.INDEX_FAILED.value,
                "message": ("文件已保存，但索引失败，已提交后台重试" if retry_scheduled
                            else "文件已保存，但索引失败，请稍后在索引状态页重试"),
                "retry_scheduled": retry_scheduled,
                "duplicate": False,
            }

        # 其它落库状态（pending_index / indexing / deleting 等）：如实返回实际状态，
        # 不把"失去发布权"包装成任何形式的成功（S04）
        if sync_status in (DocumentIndexStatus.PENDING_INDEX, DocumentIndexStatus.INDEXING):
            message = "文件已保存，索引处理中"
        else:
            message = f"文件已保存，当前状态：{sync_status.value}"
        return {
            "document_id": doc.id,
            "filename": filename,
            "status": sync_status.value,
            "message": message,
            "duplicate": False,
        }


def _default_source_name() -> str:
    """legacy 数据源身份：统一读真实向量库配置 collection 名（与可见性服务同源）。

    读取/撤销与查询必须使用同一数据源范围；配置缺失时明确抛错，
    不静默回退 "default"（否则 revoke 会撤销错误范围的映射）。
    """
    from app.services.document_visibility_service import _default_collection_name
    return _default_collection_name()


def _row_has_file(row) -> bool:
    """行是否具备可用的本地原文件（needs_reupload 判定）。"""
    import os as _os
    if not row.file_path:
        return False
    if not _os.path.exists(row.file_path):
        return False
    if (row.error_message or "") and "需要重新上传" in row.error_message:
        return False
    return True


def _stable_chunk_ids(user_id: str, document_id: str, split_docs,
                      generation: int = 0) -> List[str]:
    """稳定 chunk ID：所有者 + 文档ID + 代次 + 序号（R2B）。

    同一代次重跑生成相同 ID，Chroma upsert 覆盖而非追加；新代次写新 ID，
    发布成功后由 `_cleanup_superseded_generations` 回收旧代次产物，
    重复投递/两个 worker 重试不会产生重复有效向量。
    """
    return [f"{user_id}:{document_id}:g{generation}:{i}" for i in range(len(split_docs))]


async def _cleanup_superseded_generations(store, user_id: str, md5: str,
                                          document_id: str, active_generation: int,
                                          legacy_chunk_count: int) -> None:
    """发布成功后回收旧代次向量（尽力而为，失败不阻塞发布，R2D）。

    两条路径：
    1. 代次元数据过滤：generation < active_generation（覆盖代次化后的全部写入）；
    2. 旧版固定 ID 前缀 {user}:{doc}:v1:{i}（覆盖代次化之前的存量向量，
       数量取发布前的 chunk_count）。
    读取侧可见性过滤是正确性保证，本清理负责空间回收与防泄漏纵深。
    """
    try:
        await asyncio.to_thread(
            store.vectors_store.delete,
            where={"$and": [
                {"user_id": user_id},
                {"md5": md5},
                {"generation": {"$lt": int(active_generation)}},
            ]},
        )
    except Exception as e:
        logger.warning(f"【文档索引】旧代次向量清理失败（元数据过滤）: {e}")
    if legacy_chunk_count and legacy_chunk_count > 0:
        try:
            legacy_ids = [f"{user_id}:{document_id}:v1:{i}"
                          for i in range(legacy_chunk_count)]
            await asyncio.to_thread(store.vectors_store.delete, ids=legacy_ids)
        except Exception as e:
            logger.warning(f"【文档索引】旧版固定 ID 向量清理失败: {e}")


async def repo_like_get_status(session, doc_id: str, user_id: str):
    """新会话读取文档状态（P1 提交结果判定用，绕开 identity map）。"""
    from sqlalchemy import select as _select
    from app.models.document_index import DocumentIndex
    result = await session.execute(
        _select(DocumentIndex.status).where(
            DocumentIndex.id == doc_id, DocumentIndex.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def repo_like_get_active_generation(session, doc_id: str, user_id: str):
    """读取 active_generation（确认本次代次已发布）。"""
    from sqlalchemy import select as _select
    from app.models.document_index import DocumentIndex
    result = await session.execute(
        _select(DocumentIndex.active_generation).where(
            DocumentIndex.id == doc_id, DocumentIndex.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def repo_like_get_cleanup_task(session, doc_id: str, generation: int,
                                         user_id: str, md5: str):
    """读取本次发布对应的旧代次清理任务是否存在（确认发布配套持久记录）。"""
    from sqlalchemy import select as _select
    from app.models.cleanup_task import DocumentCleanupTask
    result = await session.execute(
        _select(DocumentCleanupTask.status).where(
            DocumentCleanupTask.document_id == doc_id,
            DocumentCleanupTask.generation == generation,
            DocumentCleanupTask.reason == "superseded_generation",
        )
    )
    return result.scalar_one_or_none()


async def repo_like_get_execution(session, doc_id: str, generation: int, token: str):
    """读取本次执行的持久状态（closed 才确认发布成功）。"""
    from sqlalchemy import select as _select
    from app.models.index_execution import DocumentIndexExecution
    result = await session.execute(
        _select(DocumentIndexExecution.status).where(
            DocumentIndexExecution.document_id == doc_id,
            DocumentIndexExecution.generation == generation,
            DocumentIndexExecution.token == token,
        )
    )
    return result.scalar_one_or_none()


async def confirm_publish_commit(session, *, document_id: str, user_id: str,
                                md5: str, generation: int,
                                execution_token: str) -> bool:
    """在一个数据库快照内确认一次发布事务是否完整落库。

    发布事实必须同时包含：文档归属与 md5 正确、当前代次已发布、
    本次执行身份已关闭，以及同一文档/用户/md5/代次的 superseded 清理
    任务已登记。清理任务允许处于 pending/failed/blocked/claimed/done，
    因为 worker 可能已经抢占或完成它。

    这是提交结果不确定时的唯一确认入口。使用一条带精确连接条件的
    查询，避免多次独立读取混合不同时间点的事实。
    """
    from sqlalchemy import and_, select
    from app.models.cleanup_task import CleanupTaskStatus, DocumentCleanupTask
    from app.models.document_index import DocumentIndex, DocumentIndexStatus
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus

    allowed_task_statuses = (
        CleanupTaskStatus.PENDING.value,
        CleanupTaskStatus.FAILED.value,
        CleanupTaskStatus.BLOCKED.value,
        CleanupTaskStatus.CLAIMED.value,
        CleanupTaskStatus.DONE.value,
    )
    result = await session.execute(
        select(DocumentIndex.id)
        .select_from(DocumentIndex)
        .join(
            DocumentIndexExecution,
            and_(
                DocumentIndexExecution.document_id == DocumentIndex.id,
                DocumentIndexExecution.user_id == DocumentIndex.user_id,
                DocumentIndexExecution.generation == generation,
                DocumentIndexExecution.token == execution_token,
                DocumentIndexExecution.status == ExecutionStatus.CLOSED,
            ),
        )
        .join(
            DocumentCleanupTask,
            and_(
                DocumentCleanupTask.document_id == DocumentIndex.id,
                DocumentCleanupTask.user_id == DocumentIndex.user_id,
                DocumentCleanupTask.md5 == DocumentIndex.md5,
                DocumentCleanupTask.generation == generation,
                DocumentCleanupTask.reason == "superseded_generation",
                DocumentCleanupTask.status.in_(allowed_task_statuses),
            ),
        )
        .where(
            DocumentIndex.id == document_id,
            DocumentIndex.user_id == user_id,
            DocumentIndex.md5 == md5,
            DocumentIndex.status == DocumentIndexStatus.INDEXED,
            DocumentIndex.active_generation == generation,
        )
    )
    return result.first() is not None


class _ExecutionRepository:
    """执行清单仓库（K1）——登记/关闭/查询未关闭执行。"""

    def __init__(self, session):
        self.session = session

    async def open_execution(self, *, document_id: str, user_id: str,
                             generation: int, token: str) -> None:
        from datetime import datetime, timedelta, timezone
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        self.session.add(DocumentIndexExecution(
            execution_id=str(uuid.uuid4()),
            document_id=document_id, user_id=user_id,
            generation=generation, token=token,
            status=ExecutionStatus.WRITING,
            lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(minutes=10),
        ))
        await self.session.flush()

    async def close_execution(self, *, document_id: str, generation: int,
                              token: str, status: str, reason: str) -> bool:
        from datetime import datetime, timezone
        from sqlalchemy import update
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        # P1：writer 持令牌可把 writing 和 unknown（协调标记的失联）收敛为
        # closed——协调的 unknown 只是失联观测，不否定 writer 的结果证明；
        # 但不得无条件覆盖已确认的 closed（无该状态条件）。
        # closed_at 仅表示确定关闭：status==closed 才写；unknown（失联/结果
        # 不确定）不设置关闭时间——避免把"何时观测失联"冒充"何时停止"。
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        values = {"status": status, "close_reason": reason, "lease_expires_at": None}
        if status == "closed":
            values["closed_at"] = now
        else:
            values["closed_at"] = None  # unknown 等非关闭态清空关闭时间
        result = await self.session.execute(
            update(DocumentIndexExecution)
            .where(DocumentIndexExecution.document_id == document_id)
            .where(DocumentIndexExecution.generation == generation)
            .where(DocumentIndexExecution.token == token)
            .where(DocumentIndexExecution.status.in_([
                ExecutionStatus.WRITING, ExecutionStatus.UNKNOWN]))
            .values(**values)
        )
        await self.session.flush()
        return result.rowcount > 0

    async def mark_expired_writers_unknown(self, limit: int = 50) -> int:
        """P1 协调：租约过期的 writing 执行标记为失联 unknown（公平有界）。

        失联不证明远端停止——关联清理任务在执行真正关闭前不得 done；
        writer 持令牌仍可自行收敛。
        """
        from datetime import datetime, timezone
        from sqlalchemy import select as _select, update
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expired_ids = list((await self.session.execute(
            _select(DocumentIndexExecution.execution_id)
            .where(DocumentIndexExecution.status == ExecutionStatus.WRITING)
            .where(DocumentIndexExecution.lease_expires_at.is_not(None))
            .where(DocumentIndexExecution.lease_expires_at < now)
            .order_by(DocumentIndexExecution.started_at.asc())
            .limit(max(1, min(int(limit), 200)))
        )).scalars().all())
        if not expired_ids:
            return 0
        result = await self.session.execute(
            update(DocumentIndexExecution)
            .where(DocumentIndexExecution.execution_id.in_(expired_ids))
            .where(DocumentIndexExecution.status == ExecutionStatus.WRITING)
            .values(status=ExecutionStatus.UNKNOWN,
                    close_reason="execution lease expired (writer lost)")
        )
        await self.session.flush()
        return result.rowcount

    async def has_open_executions(self, document_id: str) -> bool:
        from sqlalchemy import select
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        result = await self.session.execute(
            select(DocumentIndexExecution.execution_id).where(
                DocumentIndexExecution.document_id == document_id,
                DocumentIndexExecution.status != ExecutionStatus.CLOSED,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def resolve_unknown(self, execution_id: str, reason: str) -> bool:
        """unknown 的解决路径（K1）：人工核验后经此关闭，带审计原因。

        只允许 unknown → closed；writing（可能仍在写）不可经此关闭。
        调用前提：维护窗口、写入口已停、远端请求已排空（方案 §3.3）。
        """
        from datetime import datetime, timezone
        from sqlalchemy import update
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        result = await self.session.execute(
            update(DocumentIndexExecution)
            .where(DocumentIndexExecution.execution_id == execution_id)
            .where(DocumentIndexExecution.status == ExecutionStatus.UNKNOWN)
            .values(status="closed",
                    closed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    close_reason=f"resolved: {reason[:180]}")
        )
        await self.session.flush()
        return result.rowcount > 0


async def execute_index_document(document_id: str, user_id: str) -> dict:
    """统一索引执行服务（R2C）：HTTP 同步模式与 Celery worker 共用唯一实现。

    返回 {"status": DocumentIndexStatus, "retryable": bool, "error": str|None}：
    - status：数据库最终真实状态（诚实契约，发布被拒返回实际状态）；
    - retryable=True：可重试的执行失败（失败状态已条件落库，调用方可重置
      pending 后重新投递）；
    - retryable=False：终态（成功/不可重试失败/未抢到执行权/失去执行权）。
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    generation: Optional[int] = None
    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)
        try:
            doc = await repo.get_by_id(document_id, user_id)
            if not doc:
                logger.warning(f"【文档索引】文档不存在（可能已被删除），跳过: id={document_id}")
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": "document_not_found"}
            # 提交异常后原 ORM 会话会 rollback/close；后续确认只能使用这些
            # 已复制的普通值，不能再读取可能失效的 doc 属性。
            document_md5 = str(doc.md5)

            # 原子抢占 INDEXING（仅 pending_index 可进入），同时取得执行代次
            generation = await repo.claim_for_indexing(document_id)
            if generation is None:
                logger.info(f"【文档索引】未抢占到索引权（可能已在执行或删除中），跳过: id={document_id}")
                current = await repo.get_status(document_id, user_id)
                await session.commit()
                return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": None}

            # K1：同一事务登记执行清单（writing）——写入者生命周期的持久状态
            execution_repo = _ExecutionRepository(session)
            execution_token = str(uuid.uuid4())
            await execution_repo.open_execution(
                document_id=document_id, user_id=user_id,
                generation=generation, token=execution_token)
            await session.commit()

            # 检查文件是否存在（此时必然处于 indexing，失败写回由本执行方持有）
            if not os.path.exists(doc.file_path):
                error_msg = f"文件已丢失: {doc.file_path}"
                logger.error(f"【文档索引】{error_msg}, id={document_id}")
                await repo.mark_index_failed_if_indexing(
                    document_id, error_msg, generation=generation)
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason=error_msg[:200])
                await session.commit()
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": error_msg}

            from app.rag.vector_store import VectorStoreService
            store = VectorStoreService()

            # 加载文档
            documents = await store.get_file_document(
                doc.file_path, md5=document_md5, user_id=user_id
            )
            if not documents:
                if not await repo.mark_index_failed_if_indexing(
                    document_id, "文档加载为空", generation=generation
                ):
                    # 删除获胜：写入者已停止且未写入向量 → 可关闭自身记录
                    await execution_repo.close_execution(
                        document_id=document_id, generation=generation,
                        token=execution_token, status="closed",
                        reason="文档加载为空（删除获胜）")
                    current = await repo.get_status(document_id, user_id)
                    await session.commit()
                    return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                            "retryable": False, "error": None}
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason="文档加载为空")
                await session.commit()
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": "文档加载为空"}

            # 注入元数据（含执行代次，供旧代次清理与读取侧过滤使用）
            for d in documents:
                d.metadata['user_id'] = user_id
                d.metadata['original_filename'] = doc.original_filename
                d.metadata['md5'] = document_md5
                d.metadata['space_id'] = doc.space_id or ''
                d.metadata['generation'] = int(generation)
                d.metadata['document_id'] = document_id

            # 切片
            split_docs = store.split_documents_sync(documents)
            if not split_docs:
                if not await repo.mark_index_failed_if_indexing(
                    document_id, "文档切片为空", generation=generation
                ):
                    await execution_repo.close_execution(
                        document_id=document_id, generation=generation,
                        token=execution_token, status="closed",
                        reason="文档切片为空（删除获胜）")
                    current = await repo.get_status(document_id, user_id)
                    await session.commit()
                    return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                            "retryable": False, "error": None}
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason="文档切片为空")
                await session.commit()
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": "文档切片为空"}

            logger.info(f"【文档索引】文档切片完成: id={document_id}, chunks={len(split_docs)}")

            # 写入向量数据库（代次化稳定 ID，upsert 幂等）
            legacy_chunk_count = doc.chunk_count or 0
            chunk_ids = _stable_chunk_ids(user_id, document_id, split_docs, generation)

            # N0：记录可核验原文锚点（与索引同代次；发布前保持 pending，
            # 发布事务内激活）。锚点失败不阻断既有索引主链路，摘要生成时
            # 会按需从原文件重建。
            try:
                from app.services.source_anchor_service import record_document_anchors
                await record_document_anchors(
                    session,
                    owner_user_id=user_id,
                    space_id=doc.space_id,
                    document_id=document_id,
                    generation=generation,
                    content_hash=document_md5,
                    source_documents=documents,
                    split_documents=split_docs,
                    chunk_ids=chunk_ids,
                )
            except Exception as anchor_err:
                logger.warning(
                    f"【文档索引】锚点记录失败（索引继续，洞察将按需重建）: "
                    f"id={document_id}, error={str(anchor_err)[:200]}"
                )

            await asyncio.to_thread(
                store.vectors_store.add_documents, split_docs, ids=chunk_ids
            )

            # 保存 MD5 记录
            await store.save_md5_hex(document_md5, doc.filename, doc.original_filename, user_id)

            # 条件发布 indexed：状态仍为 indexing 且代次匹配（执行身份）
            if not await repo.publish_indexed(
                document_id, chunk_count=len(split_docs), generation=generation
            ):
                logger.warning(
                    f"【文档索引】索引完成但文档已被删除/deleting 或代次已过期，不发布: id={document_id}"
                )
                current = await repo.get_status(document_id, user_id)
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason="publish_rejected")
                await session.commit()
                return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": None}

            # P0/C4：发布指针与旧代次清理任务在同一事务提交——
            # 提交失败两者皆回滚（旧向量原样保留，文档保持旧代次可检索）；
            # 提交成功后清理任务进入持久队列，由公平领取的 worker 执行
            # P0/C4：发布指针 + 撤销旧映射 + 旧代次清理任务同一事务提交——
            # 提交失败三者皆回滚；提交成功后清理任务进入持久队列
            try:
                from app.repositories.legacy_mapping_repository import LegacyMappingRepository
                await LegacyMappingRepository(session).revoke(
                    _default_source_name(), user_id, document_id)
            except Exception as _revoke_err:
                logger.warning(
                    f"【文档索引】发布撤销旧映射失败（读取端仍按代次校验兜底）: {_revoke_err}"
                )

            from app.repositories.cleanup_task_repository import CleanupTaskRepository
            cleanup_repo = CleanupTaskRepository(session)
            async with session.begin_nested():
                await cleanup_repo.create_task(
                    document_id=document_id, user_id=user_id, md5=document_md5,
                    generation=generation, reason="superseded_generation",
                    legacy_chunk_count=legacy_chunk_count,
                )
                # K1：发布成功即确定关闭写入者（同一事务）
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason="published")
                # N0：同一事务激活本代次来源版本；旧版本/旧产物/旧证据失效
                from app.services.source_anchor_service import activate_document_version
                await activate_document_version(
                    session, owner_user_id=user_id,
                    document_id=document_id, generation=generation)
            try:
                await session.commit()
            except Exception as commit_err:
                # P1：提交结果不明。先释放发布会话，再以单查询在独立
                # 会话内核对本次文档/代次/执行身份/清理任务的完整事实。
                commit_err_msg = str(commit_err)[:200]
                try:
                    await session.rollback()
                except Exception:
                    pass
                try:
                    await session.close()
                except Exception:
                    pass

                try:
                    async with AsyncSessionLocal() as verify_session:
                        published_ok = await confirm_publish_commit(
                            verify_session,
                            document_id=document_id,
                            user_id=user_id,
                            md5=document_md5,
                            generation=generation,
                            execution_token=execution_token,
                        )
                except Exception as read_err:
                    # 新连接读取失败：不能推断回滚；保留 unknown 待协调
                    logger.error(
                        f"【文档索引】提交确认读取失败，待协调: id={document_id}, "
                        f"error={str(read_err)[:200]}"
                    )
                    published_ok = False

                if published_ok:
                    logger.warning(
                        f"【文档索引】发布事务确认丢失但事实已提交: id={document_id}, "
                        f"gen={generation}"
                    )
                    return {"status": DocumentIndexStatus.INDEXED,
                            "retryable": False, "error": None}

                logger.error(
                    f"【文档索引】发布事务提交失败（未持久化）: id={document_id}, "
                    f"error={commit_err_msg}"
                )
                try:
                    async with AsyncSessionLocal() as u_session:
                        await _ExecutionRepository(u_session).close_execution(
                            document_id=document_id, generation=generation,
                            token=execution_token, status="unknown",
                            reason="publish commit failed, result unconfirmed")
                        await u_session.commit()
                except Exception:
                    pass
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": True, "error": "publish_commit_uncertain"}

            logger.info(
                f"【文档索引】同步索引完成: id={document_id}, chunks={len(split_docs)}, "
                f"generation={generation}"
            )
            return {"status": DocumentIndexStatus.INDEXED,
                    "retryable": False, "error": None}

        except asyncio.CancelledError:
            # P1：取消是独立分支——写入结果不确定 → 独立短会话标记 unknown
            # 后向上传播（不转成功、不吞取消）
            try:
                async with AsyncSessionLocal() as u_session:
                    await _ExecutionRepository(u_session).close_execution(
                        document_id=document_id, generation=generation or 0,
                        token=execution_token or "", status="unknown",
                        reason="execution cancelled, remote result unknown")
                    await u_session.commit()
            except Exception:
                pass  # 收尾失败由协调任务的失联判定兜底
            raise
        except Exception as e:
            from app.tasks.index_task import _sanitize_error_message
            error_msg = _sanitize_error_message(str(e))
            logger.error(f"【文档索引】索引执行失败: id={document_id}, error={error_msg}")
            # K1：异常路径结果不确定（远端可能已写入）→ 保持 unknown
            try:
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation or 0,
                    token=execution_token or "", status="unknown",
                    reason=error_msg[:200])
                await session.commit()
            except Exception:
                await session.rollback()
            written = False
            try:
                written = await repo.mark_index_failed_if_indexing(
                    document_id, error_msg[:500], generation=generation,
                )
                await session.commit()
            except Exception:
                await session.rollback()
            if written:
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": True, "error": error_msg[:200]}
            # 失去执行权（deleting/已删除/代次过期）：状态归删除方所有
            try:
                current = await repo.get_status(document_id, user_id)
                await session.commit()
            except Exception:
                current = None
            return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                    "retryable": False, "error": error_msg[:200]}


async def reconcile_index_execution(*, document_id: str, user_id: str,
                                        generation: int,
                                        execution_token: str) -> dict:
    """N1 恢复编排：把"提交未确认、执行已核验停止"的悬挂索引恢复到可重试协议。

    恢复判定表（方案 §5.2）：
    - 执行未关闭（writing/unknown 未证明停止）→ waiting，不恢复；
    - 确认已发布（confirm_publish_commit）→ published，幂等不重置；
    - 文档已不在本次 indexing（删除/新代次/其它状态）→ conflict，不覆盖；
    - 执行 closed + 确认未发布 + 文档仍为本次 indexing → 条件恢复 index_failed，
      使现有重试协议（Celery 重试 / Beat 补偿 / reindex / 前端按钮）可接手。
    原文件缺失 → error_message 注明 needs_reupload；文件存在 → 可直接走重试。

    所有变更用带 owner/document/generation/源状态 的条件更新，两个恢复者
    同时操作最多一方推进（rowcount）；重放不会重复分配代次。
    """
    from sqlalchemy import and_, select, update
    from app.models.document_index import DocumentIndex, DocumentIndexStatus
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from datetime import datetime, timezone
    import os as _os
    from app.db.db_config import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # 1) 执行身份：必须已关闭（人工核验停止证明）才可恢复
        exec_result = await session.execute(
            select(DocumentIndexExecution.status).where(and_(
                DocumentIndexExecution.document_id == document_id,
                DocumentIndexExecution.user_id == user_id,
                DocumentIndexExecution.generation == generation,
                DocumentIndexExecution.token == execution_token,
            ))
        )
        exec_status = exec_result.scalar_one_or_none()
        if exec_status != ExecutionStatus.CLOSED.value:
            await session.commit()
            return {"outcome": "waiting",
                    "message": "执行未关闭，等待核验停止证明"}

        # 2) 确认发布事实：已发布则幂等返回，不重置不删向量
        from app.models.document_index import DocumentIndex as _DI
        doc_result = await session.execute(
            select(_DI.md5, _DI.status, _DI.generation, _DI.file_path).where(and_(
                _DI.id == document_id, _DI.user_id == user_id))
        )
        doc_row = doc_result.one_or_none()
        if doc_row is None:
            await session.commit()
            return {"outcome": "gone", "message": "文档不存在（已删除），不恢复"}
        doc_md5, doc_status, doc_gen, file_path = doc_row

        if doc_status == DocumentIndexStatus.INDEXED.value                 and int(doc_gen or 0) == int(generation):
            # 已发布：确认发布事实完整
            published_ok = await confirm_publish_commit(
                session, document_id=document_id, user_id=user_id,
                md5=doc_md5, generation=generation,
                execution_token=execution_token)
            await session.commit()
            if published_ok:
                return {"outcome": "published",
                        "message": "确认已发布，保持现状（幂等）"}

        # 3) 文档必须仍为本次 indexing 才恢复（删除/新代次/其它状态 → 冲突不覆盖）
        if doc_status != DocumentIndexStatus.INDEXING.value                 or int(doc_gen or 0) != int(generation):
            # 幂等重放：若已由本次恢复完成（index_failed + 本 recover 身份 + 代次匹配），
            # 重建 recovered 结果，不误报 conflict
            if doc_status == DocumentIndexStatus.INDEX_FAILED.value                     and int(doc_gen or 0) == int(generation):
                # 结构化恢复凭据：本次执行行已带恢复事实（recovered_at 非空）
                rec = (await session.execute(
                    select(DocumentIndexExecution.recovery_outcome,
                           DocumentIndexExecution.recovered_at).where(and_(
                        DocumentIndexExecution.document_id == document_id,
                        DocumentIndexExecution.user_id == user_id,
                        DocumentIndexExecution.generation == int(generation),
                        DocumentIndexExecution.token == execution_token,
                    ))
                )).one_or_none()
                if rec is not None and rec[1] is not None:
                    replay_has_file = bool(file_path) and _os.path.exists(file_path)
                    outcome = rec[0] or ("recovered" if replay_has_file
                                         else "needs_reupload")
                    await session.commit()
                    return {"outcome": outcome,
                            "message": "已由本次恢复完成（幂等，结构化事实）"}
            await session.commit()
            return {"outcome": "conflict",
                    "message": f"文档状态/代次已变化（{doc_status}/gen={doc_gen}），不覆盖"}

        # 4) 条件恢复 index_failed（带 owner/document/源状态/代次条件）；
        #    错误文案携带本次恢复身份（唯一令牌 + 代次），供确认与重放区分
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 结构化恢复事实写入执行行（同事务）；错误文案仅展示给人，不作业务凭据。
        has_file = bool(file_path) and _os.path.exists(file_path)
        recovery_outcome = "recovered" if has_file else "needs_reupload"
        error_note = ("publish commit unconfirmed, writer verified stopped"
                      if has_file else
                      "publish commit unconfirmed; 原文件缺失，需要重新上传")
        result = await session.execute(
            update(DocumentIndex)
            .where(and_(
                DocumentIndex.id == document_id,
                DocumentIndex.user_id == user_id,
                DocumentIndex.status == DocumentIndexStatus.INDEXING,
                DocumentIndex.generation == int(generation),
            ))
            .values(
                status=DocumentIndexStatus.INDEX_FAILED,
                error_message=error_note,
                updated_at=now,
            )
        )
        # 结构化恢复凭据（重放/确认据此判定，不依赖文案子串）
        await session.execute(
            update(DocumentIndexExecution)
            .where(and_(
                DocumentIndexExecution.document_id == document_id,
                DocumentIndexExecution.user_id == user_id,
                DocumentIndexExecution.generation == int(generation),
                DocumentIndexExecution.token == execution_token,
                DocumentIndexExecution.status == ExecutionStatus.CLOSED.value,
            ))
            .values(
                recovery_outcome=recovery_outcome,
                recovered_at=now,
            )
        )
        try:
            await session.commit()
        except Exception as commit_err:
            # 恢复事务提交结果不明：先释放会话，再用新会话读持久事实——
            # 若文档已是 index_failed 且 error 为本恢复标记，则幂等重建"已恢复"，
            # 不因确认丢失误报 conflict，也不二次分配代次。
            commit_err_msg = str(commit_err)[:200]
            try:
                await session.rollback()
            except Exception:
                pass
            try:
                await session.close()
            except Exception:
                pass
            async with AsyncSessionLocal() as v_session:
                v_row = (await v_session.execute(
                    select(DocumentIndex.status, DocumentIndex.error_message,
                           DocumentIndex.generation).where(
                        DocumentIndex.id == document_id,
                        DocumentIndex.user_id == user_id,
                    )
                )).one_or_none()
                v_rec = (await v_session.execute(
                    select(DocumentIndexExecution.recovery_outcome,
                           DocumentIndexExecution.recovered_at).where(and_(
                        DocumentIndexExecution.document_id == document_id,
                        DocumentIndexExecution.user_id == user_id,
                        DocumentIndexExecution.generation == int(generation),
                        DocumentIndexExecution.token == execution_token,
                    ))
                )).one_or_none()
            # 确认绑定本次恢复身份：状态 index_failed + 代次匹配 +
            # 结构化恢复事实存在（recovered_at 非空）——其它代次/其它恢复不误认
            if v_row is not None                     and v_row[0] == DocumentIndexStatus.INDEX_FAILED.value                     and int(v_row[2] or 0) == int(generation)                     and v_rec is not None and v_rec[1] is not None:
                logger.warning(
                    f"【文档索引】恢复事务确认丢失但事实已提交: id={document_id}, "
                    f"gen={generation}, err={commit_err_msg}"
                )
                return {"outcome": "needs_reupload" if not has_file else "recovered",
                        "message": error_note}
            logger.error(
                f"【文档索引】恢复事务提交失败（未持久化）: id={document_id}, "
                f"err={commit_err_msg}"
            )
            return {"outcome": "uncertain",
                    "message": "恢复提交结果不明，请重试"}
        if result.rowcount == 0:
            return {"outcome": "conflict",
                    "message": "恢复条件未命中（并发删除/新代次/其它恢复者已推进）"}
        return {"outcome": "needs_reupload" if not has_file else "recovered",
                "message": error_note}


async def _sync_index(document_id: str, user_id: str) -> DocumentIndexStatus:
    """
    同步索引（HTTP 请求内执行的降级方案）—— 统一执行服务的薄封装（R2C）。

    返回数据库最终真实状态：indexed / index_failed / deleting / pending_index 等。
    条件发布被拒（删除获胜）时如实返回当前状态，绝不伪报 indexed（S04）。
    """
    result = await execute_index_document(document_id, user_id)
    return result["status"]


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

        # 条件重置 pending_index（S02 TOCTOU：读取时是 failed/pending，
        # 更新前可能已被置为 deleting/indexing —— 以条件更新行数为准）
        reset_ok = await repo.reset_to_pending_if_retryable(document_id)
        if not reset_ok:
            await session.commit()
            logger.warning(f"【文档索引】人工重试重置被拒（状态已变化）: id={document_id}")
            return {"success": False, "message": "文档状态已变化（可能正在删除或已被删除），请刷新后重试"}
        await repo.increment_retry(document_id)
        await session.commit()

        # 提交成功之后才投递 Celery 任务；broker 失败由 Beat 补偿发现
        from app.tasks.index_task import index_document_task
        try:
            index_document_task.delay(document_id, user_id)
            return {"success": True, "message": "已提交重新索引任务"}
        except Exception as e:
            logger.warning(f"【文档索引】提交 Celery 任务失败: {e}")
            # 降级为同步索引
            try:
                sync_status = await _sync_index(document_id, user_id)
                if sync_status == DocumentIndexStatus.INDEXED:
                    return {"success": True, "message": "已同步完成重新索引"}
                return {"success": False, "message": "重新索引失败，请查看状态后重试"}
            except Exception as sync_err:
                logger.error(f"【文档索引】同步索引也失败: {sync_err}")
                return {"success": False, "message": "索引失败，请稍后重试"}


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


async def get_active_document_md5s(user_id: str, md5s) -> dict:
    """查询仍对外可见（indexed）的文档：md5 → active_generation（R2D 读取侧过滤）。

    查询失败时 fail-closed 返回空映射 —— 不可验证的切片不进入 rerank/
    模型上下文，防止已删除/已重建文档的旧切片复活。
    """
    if not md5s:
        return {}
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository
    try:
        async with AsyncSessionLocal() as session:
            repo = DocumentIndexRepository(session)
            return await repo.get_indexed_md5s(user_id, list(md5s))
    except Exception as e:
        logger.warning(f"【文档索引】读取侧可见性查询失败（fail-closed）: {e}")
        return {}


async def delete_index_record(document_id: str, user_id: str, delete_file: bool = True) -> dict:
    """
    删除单个文档索引记录（v2 路径）。单删与批删共用同一编排（S03）。

    删除顺序（保证一致性）：
    1. 查询 DocumentIndex（按 document_id + user_id）
    2. 置 deleting（并发索引任务无法再 claim，读取侧可见"删除中"）
    3. 按 user_id + md5 删除 Chroma 向量和旧 MD5 记录
    4. 安全删除 knowledge_files 下的物理文件（校验必须在用户目录内）
    5. 删除 document_index 记录并显式 commit

    幂等：文档不存在也返回成功。
    返回：{"success": bool, "state": "deleted"|"deleting"|"failed", "message": str}
    失败时保留可恢复依据（记录不删、状态停在 deleting），可重试。
    """
    from app.rag.vector_store import VectorStoreService
    try:
        store = VectorStoreService()
    except Exception as e:
        logger.error(f"【文档索引】VectorStoreService 初始化失败，拒绝删除（可能漏清向量）: {e}")
        return {"success": False, "state": "failed",
                "message": "向量服务不可用，删除未执行，请稍后重试"}
    return await _delete_single_record(document_id, user_id, delete_file, store)


async def _delete_single_record(doc_id: str, user_id: str, delete_file: bool, store) -> dict:
    """单条删除编排 —— 唯一实现，所有删除入口共用。

    返回 {"success", "state": deleted|deleting|failed, "message"}：
    - deleted：向量/MD5/文件/SQL 全部确认完成；
    - deleting：清理未完成（向量/MD5/文件失败），记录保留可重试；
    - failed：SQL 删除等不可自动恢复的失败。
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        doc = await repo.get_by_id(doc_id, user_id)
        if not doc:
            return {"success": True, "state": "deleted",
                    "message": "文档不存在或已删除（幂等）"}

        saved_md5 = doc.md5
        saved_status = doc.status
        file_path = doc.file_path

        # 先置为 DELETING（幂等）：并发索引任务无法再 claim，读取侧可见"删除中"
        if saved_status != DocumentIndexStatus.DELETING:
            await repo.update_status(doc_id, DocumentIndexStatus.DELETING)
            await session.commit()

        # 向量清理（K2 精确目标）：按 user+document_id 定位本资源向量
        # （切片元数据携带 document_id），不按 user+md5 盲删——同内容其它
        # 文档/空间的资源不受影响；pre-008 旧版固定 ID 按 chunk_count 清单补删
        if store is not None:
            try:
                where_clause = {"$and": [{"user_id": user_id},
                                         {"document_id": doc_id}]}
                await asyncio.to_thread(store.vectors_store.delete, where=where_clause)
                if int(doc.chunk_count or 0) > 0:
                    legacy_ids = [f"{user_id}:{doc_id}:v1:{i}"
                                  for i in range(int(doc.chunk_count))]
                    await asyncio.to_thread(store.vectors_store.delete, ids=legacy_ids)
                logger.info(f"【文档索引】已删除向量: user_id={user_id}, document_id={doc_id}")
            except Exception as e:
                # 向量清理未完成：保留 deleting 记录（可重试），不得先删 SQL 记录导致
                # "向量仍可检索但记录已删除"的复活/残留
                error_msg = str(e)[:300]
                logger.error(f"【文档索引】向量清理失败，保留 deleting 记录: {error_msg}")
                await repo.update_status(
                    doc_id,
                    DocumentIndexStatus.DELETING,
                    error_message=f"向量清理失败: {error_msg}",
                )
                await session.commit()
                return {"success": False, "state": "deleting",
                        "message": "向量清理未完成，记录保留（deleting），可重试"}

            try:
                from app.services.knowledge_record_service import KnowledgeRecordService
                record_service = KnowledgeRecordService()
                await record_service.delete_single_md5(user_id, saved_md5, delete_documents=False)
            except Exception as e:
                # MD5 清理失败同样保留可恢复依据（可重试），不宣称成功
                logger.warning(f"【文档索引】删除 MD5 记录失败，保留 deleting 记录: {e}")
                await repo.update_status(
                    doc_id,
                    DocumentIndexStatus.DELETING,
                    error_message=f"MD5 记录清理失败: {str(e)[:200]}",
                )
                await session.commit()
                return {"success": False, "state": "deleting",
                        "message": "MD5 记录清理未完成，记录保留（deleting），可重试"}

        # 安全删除物理文件；失败保留记录（可重试），不删 SQL 丢失清理依据
        if delete_file and file_path:
            if not _safe_delete_physical_file(file_path, user_id):
                logger.error(f"【文档索引】物理文件删除失败，保留 deleting 记录: {file_path}")
                await repo.update_status(
                    doc_id,
                    DocumentIndexStatus.DELETING,
                    error_message="物理文件删除失败，记录保留可重试",
                )
                await session.commit()
                return {"success": False, "state": "deleting",
                        "message": "物理文件删除未完成，记录保留（deleting），可重试"}

        # C4/评审 #1：删除记录与持久清理任务在同一事务提交——
        # 中途崩溃不会出现"删除成功但清理依据永久丢失"
        try:
            # 复验修正：删除 → 撤销该文档旧映射（旧映射随资源删除失效）
            try:
                from app.repositories.legacy_mapping_repository import LegacyMappingRepository
                legacy_repo = LegacyMappingRepository(session)
                await legacy_repo.revoke(_default_source_name(), user_id, doc_id)
            except Exception as _revoke_err:
                logger.warning(
                    f"【文档索引】删除撤销旧映射失败（读取端仍按代次校验兜底）: {_revoke_err}"
                )

            from app.repositories.cleanup_task_repository import CleanupTaskRepository
            cleanup_repo = CleanupTaskRepository(session)
            async with session.begin_nested():
                await cleanup_repo.create_task(
                    document_id=doc_id, user_id=user_id, md5=saved_md5,
                    generation=0,  # 哨兵：文档已删
                    reason="document_deleted",
                    legacy_chunk_count=int(doc.chunk_count or 0),
                )
            # N0：来源版本/产物/证据与删除同事务失效；锚点保留审计但不可读
            from app.services.source_anchor_service import invalidate_document
            await invalidate_document(
                session, owner_user_id=user_id, document_id=doc_id, invalid=True)
            await repo.delete_by_id(doc_id, user_id)
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"【文档索引】删除记录失败: id={doc_id}, error={e}")
            return {"success": False, "state": "failed",
                    "message": f"记录删除失败: {str(e)[:200]}"}

        logger.info(f"【文档索引】已删除记录: id={doc_id}")
        return {"success": True, "state": "deleted", "message": "文档已删除"}


def _is_within(child_real: str, parent_real: str) -> bool:
    """路径级目录边界判断：child 必须在 parent 内（处理同前缀兄弟目录、目录本身、跨盘符）。"""
    try:
        return os.path.commonpath([child_real, parent_real]) == parent_real
    except ValueError:
        # 跨盘符（Windows）等无法求公共路径 → 判定为不在目录内
        return False


def _safe_delete_physical_file(file_path: str, user_id: str) -> bool:
    """安全删除物理文件，校验 realpath 必须在用户目录内且为普通文件。

    返回可判定结果（S03）：True = 文件已不存在（删除成功或原本就没有）；
    False = 文件仍在但无法删除（路径不合法、非普通文件或删除失败）。
    """
    if not file_path or not os.path.exists(file_path):
        return True  # 资源原本不存在：幂等成功

    try:
        user_dir = _get_storage_dir(user_id)
        real_file = os.path.realpath(file_path)
        real_user_dir = os.path.realpath(user_dir)

        # startswith 不是可靠目录边界（/user1 与 /user10、目录本身等），
        # 改用 commonpath 路径级比较；且仅删除普通文件（不删目录/符号链接目标）
        if real_file == real_user_dir:
            logger.warning(f"【文档索引】拒绝删除用户目录本身: {file_path}")
            return False
        if not _is_within(real_file, real_user_dir):
            logger.warning(f"【文档索引】文件路径不在用户目录内，拒绝删除: {file_path}")
            return False
        if not os.path.isfile(real_file):
            logger.warning(f"【文档索引】目标不是普通文件，拒绝删除: {file_path}")
            return False

        os.unlink(real_file)
        logger.info(f"【文档索引】已删除物理文件: {file_path}")
        return True
    except Exception as e:
        logger.warning(f"【文档索引】删除物理文件失败: {e}")
        return False


async def clean_user_index_records(user_id: str, space_id: str = None) -> dict:
    """
    清除用户的所有 v2 文档索引记录、向量和物理文件（S03 逐项编排）。

    批删只负责枚举和汇总：逐条调用与单删相同的 `_delete_single_record`，
    每条独立提交；只有确认清理完成的条目计入 deleted_count，其余保留
    可恢复依据（deleting）可重试。

    参数：
        user_id: 用户ID
        space_id: 空间ID（None 表示清理所有）

    返回：
        dict: {"success", "deleted_count", "failed_count", "results", "message"}
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)
        docs = await repo.get_user_documents(user_id, space_id=space_id)

    if not docs:
        return {"success": True, "deleted_count": 0, "failed_count": 0,
                "results": [], "message": "没有需要清除的记录"}

    # 共享一个向量库实例；初始化失败时全部条目只标记 deleting，
    # 不删除任何 SQL/文件（向量无法清理，保留可恢复依据）
    store = None
    store_error = ""
    try:
        from app.rag.vector_store import VectorStoreService
        store = VectorStoreService()
    except Exception as e:
        store_error = str(e)[:200]
        logger.warning(f"【文档索引】VectorStoreService 初始化失败（可能 Chroma 不可用）: {store_error}")

    results = []
    deleted_count = 0
    failed_count = 0

    if store is None:
        async with AsyncSessionLocal() as session:
            mark_repo = DocumentIndexRepository(session)
            for doc in docs:
                await mark_repo.update_status(
                    doc.id, DocumentIndexStatus.DELETING,
                    error_message=f"向量服务不可用: {store_error}",
                )
                results.append({
                    "document_id": doc.id,
                    "filename": doc.original_filename,
                    "state": "deleting",
                    "message": "向量服务不可用，记录保留（deleting），可重试",
                })
            await session.commit()
        failed_count = len(docs)
    else:
        for doc in docs:
            out = await _delete_single_record(doc.id, user_id, delete_file=True, store=store)
            results.append({
                "document_id": doc.id,
                "filename": doc.original_filename,
                "state": out["state"],
                "message": out["message"],
            })
            if out["state"] == "deleted":
                deleted_count += 1
            else:
                failed_count += 1

    message = f"已删除 {deleted_count} 条记录"
    if failed_count:
        message += f"，{failed_count} 条清理未完成（可重试）"
    logger.info(f"【文档索引】已清除用户 {user_id} 的 v2 记录: {message}")

    return {"success": failed_count == 0, "deleted_count": deleted_count,
            "failed_count": failed_count, "results": results, "message": message}
