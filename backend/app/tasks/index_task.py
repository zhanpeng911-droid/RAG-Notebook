"""
文档后台索引任务 —— 由 Celery 执行的异步向量化任务。

当 embedding 服务可用时，后台 worker 会：
1. 从 MySQL 获取待索引文档记录
2. 加载文档并切片
3. 生成向量嵌入并写入 ChromaDB
4. 更新索引状态为 indexed 或 index_failed
"""
import asyncio

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
    异步索引文档的核心逻辑 —— 统一执行服务的 Celery 封装（R2C）。

    执行器（app.services.document_index_service.execute_index_document）内部完成：
    1. 原子抢占 INDEXING（仅 pending_index 可进入），同时取得执行代次（R2B）
    2. 检查文件、加载文档并切片、写入代次化向量、保存 MD5 记录
    3. 条件发布 indexed（状态 + 代次匹配），发布后回收旧代次向量（R2D）
    4. 失败时条件写回 index_failed（不覆盖 deleting/删除）

    错误处理（本封装职责）：
    - retryable 失败且仍有重试预算：条件重置回 pending_index（仅 index_failed
      可重置）+ 递增重试计数，重置与提交成功后才投递 Celery 重试（S02/R3）
    - 失去执行权（deleting/删除/代次过期）：安全退出，不投递重试
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.document_index_repository import DocumentIndexRepository
    from app.services.document_index_service import execute_index_document

    result = await execute_index_document(document_id, user_id)

    if not result["retryable"]:
        return

    error = result.get("error") or "索引执行失败"
    logger.error(f"【后台索引】文档索引失败: id={document_id}, error={error}")

    if task.request.retries < task.max_retries:
        async with AsyncSessionLocal() as session:
            repo = DocumentIndexRepository(session)
            try:
                if not await repo.reset_to_pending_if_failed(document_id):
                    logger.warning(
                        f"【后台索引】重试前重置被拒（状态已变化），不投递重试: id={document_id}"
                    )
                    await session.commit()
                    return
                await repo.increment_retry(document_id)
                await session.commit()
            except Exception as reset_err:
                await session.rollback()
                logger.error(f"【后台索引】重试前重置为 pending 失败: {reset_err}")
                return
        raise task.retry(exc=RuntimeError(error))


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

    async with AsyncSessionLocal() as session:
        repo = DocumentIndexRepository(session)

        # 获取 pending_index 文档
        pending_docs = await repo.get_pending_documents(limit=limit)

        # 获取可重试的失败文档（retry_count < 3）
        from sqlalchemy import select, and_
        from app.models.document_index import DocumentIndex, DocumentIndexStatus
        result = await session.execute(
            select(DocumentIndex).where(
                and_(
                    DocumentIndex.status == DocumentIndexStatus.INDEX_FAILED,
                    DocumentIndex.retry_count < 3,
                )
            ).order_by(DocumentIndex.created_at.asc()).limit(limit)
        )
        failed_docs = list(result.scalars().all())

        if not pending_docs and not failed_docs:
            logger.info("【批量索引】没有待索引的文档")
            return

        # 条件重置失败文档（仅 index_failed 可重置；读取后被删除/deleting 抢占的跳过），
        # 全部提交成功之后才允许投递（S02：不能先投递后提交）
        eligible = {doc.id: doc.user_id for doc in pending_docs}
        for doc in failed_docs:
            if await repo.reset_to_pending_if_failed(doc.id):
                eligible[doc.id] = doc.user_id
            else:
                logger.warning(
                    f"【批量索引】失败文档重置被拒（状态已变化，跳过）: id={doc.id}"
                )
        await session.commit()

        logger.info(
            f"【批量索引】待索引 {len(pending_docs)} 个 + 可重试 {len(failed_docs)} 个，"
            f"实际投递 {len(eligible)} 个"
        )

        # 提交成功后统一投递；broker 失败的文档仍留在 pending_index，
        # 由下一轮 Beat 补偿发现（数据库是唯一事实来源）
        for doc_id, doc_user_id in eligible.items():
            try:
                index_document_task.delay(doc_id, doc_user_id)
            except Exception as e:
                logger.error(f"【批量索引】提交任务失败: id={doc_id}, error={e}")
