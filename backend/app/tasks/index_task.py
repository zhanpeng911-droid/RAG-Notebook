"""
文档后台索引任务 —— 由 Celery 执行的异步向量化任务。

当 embedding 服务可用时，后台 worker 会：
1. 从 MySQL 获取待索引文档记录
2. 加载文档并切片
3. 生成向量嵌入并写入 ChromaDB
4. 更新索引状态为 indexed 或 index_failed
"""
import asyncio
import os
import tempfile

from app.tasks.celery_app import celery_app
from app.core.logger_handler import logger


def _run_async(coro):
    """在同步 Celery worker 中运行异步代码"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, ignore_result=True)
def index_document_task(self, document_id: str, user_id: str):
    """
    后台索引单个文档。

    流程：
    1. 更新状态为 indexing
    2. 加载文档并切片
    3. 生成向量嵌入并写入 ChromaDB
    4. 保存 MD5 记录
    5. 更新状态为 indexed（成功）或 index_failed（失败）
    """
    _run_async(_index_document_async(self, document_id, user_id))


async def _index_document_async(task, document_id: str, user_id: str):
    """
    异步索引文档的核心逻辑。

    流程：
    1. 检查文档记录是否存在
    2. 检查文件是否存在
    3. 加载文档并切片
    4. 调用 embedding 生成向量并写入 ChromaDB
    5. 更新状态为 indexed 或 index_failed

    错误处理：
    - embedding 调用失败：写入 index_failed + 真实错误信息
    - 文档被删除：安全退出，不抛异常
    - 所有异常都会记录真实错误信息，不允许静默卡在 pending_index
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository
    from app.models.document_index import DocumentIndexStatus

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        # 获取文档记录（并发安全：记录可能已被删除）
        doc = await repo.get_by_id(document_id, user_id)
        if not doc:
            logger.warning(f"【后台索引】文档不存在（可能已被删除），跳过: id={document_id}")
            return

        # 检查文件是否存在
        if not os.path.exists(doc.file_path):
            error_msg = f"文件已丢失: {doc.file_path}"
            logger.error(f"【后台索引】{error_msg}, id={document_id}")
            await repo.update_status(
                document_id,
                DocumentIndexStatus.INDEX_FAILED,
                error_message=error_msg
            )
            await session.commit()
            return

        # 更新状态为 indexing
        await repo.update_status(document_id, DocumentIndexStatus.INDEXING)
        await session.commit()

        try:
            # 加载文档并切片
            from app.rag.vector_store import VectorStoreService
            store = VectorStoreService()

            # 加载文档
            logger.info(f"【后台索引】开始加载文档: id={document_id}, path={doc.file_path}")
            documents = await store.get_file_document(
                doc.file_path, md5=doc.md5, user_id=user_id
            )
            if not documents:
                error_msg = "文档加载为空（可能是不支持的文件格式或文件损坏）"
                logger.error(f"【后台索引】{error_msg}, id={document_id}")
                await repo.update_status(
                    document_id,
                    DocumentIndexStatus.INDEX_FAILED,
                    error_message=error_msg
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
                error_msg = "文档切片为空（可能是文件内容过少）"
                logger.error(f"【后台索引】{error_msg}, id={document_id}")
                await repo.update_status(
                    document_id,
                    DocumentIndexStatus.INDEX_FAILED,
                    error_message=error_msg
                )
                await session.commit()
                return

            logger.info(f"【后台索引】文档切片完成: id={document_id}, chunks={len(split_docs)}")

            # 写入向量数据库（这一步会触发真实的 embedding 调用）
            # 如果 embedding 服务不可用，这里会抛出异常
            await asyncio.to_thread(store.vectors_store.add_documents, split_docs)

            # 保存 MD5 记录
            await store.save_md5_hex(doc.md5, doc.filename, doc.original_filename, user_id)

            # 再次检查记录是否还存在（并发安全：删除和索引可能同时发生）
            still_exists = await repo.get_by_id(document_id, user_id)
            if not still_exists:
                logger.warning(f"【后台索引】文档在索引过程中被删除，不写入状态: id={document_id}")
                return

            # 更新状态为 indexed
            await repo.update_status(
                document_id,
                DocumentIndexStatus.INDEXED,
                chunk_count=len(split_docs)
            )
            await session.commit()

            logger.info(
                f"【后台索引】文档索引完成: id={document_id}, "
                f"filename={doc.original_filename}, chunks={len(split_docs)}"
            )

        except Exception as e:
            # 提取真实错误信息（不打印 API Key 等敏感信息）
            error_msg = _sanitize_error_message(str(e))
            logger.error(f"【后台索引】文档索引失败: id={document_id}, error={error_msg}")

            try:
                await repo.update_status(
                    document_id,
                    DocumentIndexStatus.INDEX_FAILED,
                    error_message=error_msg[:500]
                )
                await repo.increment_retry(document_id)
                await session.commit()
            except Exception as commit_err:
                logger.error(f"【后台索引】写入失败状态也失败: {commit_err}")

            # 重试（如果还有次数）
            if task.request.retries < task.max_retries:
                raise task.retry(exc=e)


def _sanitize_error_message(msg: str) -> str:
    """
    清理错误信息，移除可能的敏感信息（API Key、Token 等）。

    :param msg: 原始错误信息
    :return: 清理后的错误信息
    """
    import re
    # 移除可能的 API Key（sk-xxxx 格式）
    msg = re.sub(r'sk-[a-zA-Z0-9]{8,}', 'sk-***', msg)
    # 移除可能的 Bearer Token
    msg = re.sub(r'Bearer\s+[a-zA-Z0-9._-]{20,}', 'Bearer ***', msg)
    # 移除可能的密码
    msg = re.sub(r'password[=:]\s*\S+', 'password=***', msg, flags=re.IGNORECASE)
    return msg


@celery_app.task(bind=True, max_retries=1, ignore_result=True)
def batch_index_pending_task(self, limit: int = 10):
    """
    批量索引待处理文档。

    由 Celery Beat 定时调度或手动触发，从 MySQL 中获取 pending_index 状态的文档，
    逐个提交索引任务。

    也处理 index_failed 且 retry_count < 3 的文档（自动重试）。
    """
    _run_async(_batch_index_pending_async(limit))


async def _batch_index_pending_async(limit: int):
    """
    异步批量提交待索引文档。

    包括：
    1. pending_index 状态的文档
    2. index_failed 且 retry_count < 3 的文档（自动重试）
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository
    from app.models.document_index import DocumentIndexStatus

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        # 获取 pending_index 文档
        pending_docs = await repo.get_pending_documents(limit=limit)

        # 获取可重试的失败文档（retry_count < 3）
        from sqlalchemy import select, and_
        from app.models.document_index import DocumentIndex
        result = await session.execute(
            select(DocumentIndex).where(
                and_(
                    DocumentIndex.status == DocumentIndexStatus.INDEX_FAILED,
                    DocumentIndex.retry_count < 3,
                )
            ).order_by(DocumentIndex.created_at.asc()).limit(limit)
        )
        failed_docs = list(result.scalars().all())

        all_docs = pending_docs + failed_docs
        if not all_docs:
            logger.info("【批量索引】没有待索引的文档")
            return

        logger.info(f"【批量索引】找到 {len(pending_docs)} 个待索引 + {len(failed_docs)} 个可重试文档")

        for doc in all_docs:
            try:
                # 重置状态为 pending_index
                if doc.status == DocumentIndexStatus.INDEX_FAILED:
                    await repo.update_status(doc.id, DocumentIndexStatus.PENDING_INDEX)
                index_document_task.delay(doc.id, doc.user_id)
                logger.info(f"【批量索引】已提交索引任务: id={doc.id}, filename={doc.original_filename}")
            except Exception as e:
                logger.error(f"【批量索引】提交任务失败: id={doc.id}, error={e}")

        await session.commit()
