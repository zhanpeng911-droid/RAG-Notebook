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
from concurrent.futures import ThreadPoolExecutor

from app.tasks.celery_app import celery_app
from app.core.logger_handler import logger

_task_loop: asyncio.AbstractEventLoop | None = None
_task_loop_pid: int | None = None


def _run_async(coro):
    """Run async work on one persistent worker loop.

    Creating a new event loop (and disposing the SQLAlchemy engine twice) for
    every one-row note outbox task capped the solo worker near 9 tasks/s,
    while the 100 QPS mixed profile produces 20 saves/s. One loop per worker
    process keeps the DB pool valid across sequential tasks and avoids the
    per-task connection teardown that also produced 'Event loop is closed'
    noise. A PID check prevents a forked child from reusing a parent loop.
    """
    global _task_loop, _task_loop_pid
    pid = os.getpid()
    if _task_loop is None or _task_loop.is_closed() or _task_loop_pid != pid:
        _task_loop = asyncio.new_event_loop()
        _task_loop_pid = pid
        asyncio.set_event_loop(_task_loop)
        # Bound asyncio.to_thread; an unbounded default executor contributed
        # to the note worker thread growth observed in the one-hour run.
        _task_loop.set_default_executor(
            ThreadPoolExecutor(max_workers=4, thread_name_prefix="task-io"))
        from app.db.db_config import dispose_stale_connections
        _task_loop.run_until_complete(dispose_stale_connections())
    return _task_loop.run_until_complete(coro)


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
def process_cleanup_tasks_task(self, limit: int = 20):
    """公平领取并执行持久清理任务（C4）。

    队列 = document_cleanup_tasks 表（发布旧代次回收 + 删除后残余）。
    两阶段有界领取（兼容 MySQL），条件写令牌与租约；清理在事务外执行；
    成功要求按有界查询核验目标为空；失败指数退避，超限 blocked。
    """
    _run_async(_process_cleanup_tasks_async(limit))


async def _process_cleanup_tasks_async(limit: int) -> int:
    import asyncio as _aio
    from app.db.db_config import AsyncSessionLocal

    from app.repositories.cleanup_task_repository import (
        CleanupTaskRepository, UnresolvedCleanupError, WaitingWriterDeferred,
    )

    async with AsyncSessionLocal() as session:
        # K3：领取统一走仓库实现（两阶段物化 + 状态/租约复核），worker 不再自持 SQL
        repo = CleanupTaskRepository(session)
        tasks = await repo.claim_due_tasks(limit=limit)
        await session.commit()

    if not tasks:
        return 0

    try:
        from app.rag.vector_store import VectorStoreService
        store = VectorStoreService()
    except Exception as e:
        logger.warning(f"【清理任务】向量服务不可用，任务保留待下轮: {e}")
        return 0

    from app.repositories.cleanup_task_repository import (
        CleanupTaskRepository,
    )


    done = 0
    async with AsyncSessionLocal() as session:
        repo = CleanupTaskRepository(session)
        for task in tasks:
            try:
                # 评审 #3（核验顺序）：先确认写入者已全部关闭，再执行最终
                # 清理与查空——关闭之后不可能再有协议内写入，查空才是终局
                # 证据。写入者未关闭时只做挂起（不清理不查空）。
                from app.services.document_index_service import _ExecutionRepository
                if await _ExecutionRepository(session).has_open_executions(task.document_id):
                    raise WaitingWriterDeferred(
                        "存在未关闭的写入执行（waiting_writer），暂缓完成")

                # K2：目标 = 本文档身份（document_id 元数据）+ 已知旧版 ID 清单；
                # 禁止 user+md5 宽泛删除
                if task.reason == "document_deleted":
                    await _aio.to_thread(
                        store.vectors_store.delete,
                        where={"$and": [
                            {"user_id": task.user_id},
                            {"document_id": task.document_id},
                        ]},
                    )
                    if task.legacy_chunk_count and task.legacy_chunk_count > 0:
                        legacy_ids = [f"{task.user_id}:{task.document_id}:v1:{i}"
                                      for i in range(task.legacy_chunk_count)]
                        await _aio.to_thread(store.vectors_store.delete, ids=legacy_ids)
                else:
                    # superseded_generation：本文档 generation < 目标代次
                    await _aio.to_thread(
                        store.vectors_store.delete,
                        where={"$and": [
                            {"user_id": task.user_id},
                            {"document_id": task.document_id},
                            {"generation": {"$lt": int(task.generation)}},
                        ]},
                    )
                    if task.legacy_chunk_count and task.legacy_chunk_count > 0:
                        legacy_ids = [f"{task.user_id}:{task.document_id}:v1:{i}"
                                      for i in range(task.legacy_chunk_count)]
                        await _aio.to_thread(store.vectors_store.delete, ids=legacy_ids)

                # 归属分类（读取 metadata，非 ID 差集）：
                # - 属于本文档：必须为空（否则重试）
                # - 明确属于其它资源（document_id 非空且不同）：不是遗留，跳过
                # - 无 document_id 的同 md5 向量：无法归属 → blocked 待映射
                remain_all = await _aio.to_thread(
                    store.vectors_store.get,
                    where={"$and": [{"user_id": task.user_id}, {"md5": task.md5}]},
                    include=["metadatas"],
                )
                metas = remain_all.get("metadatas", []) if isinstance(remain_all, dict) else []
                own_left = 0
                unattributed = 0
                is_superseded = task.reason == "superseded_generation"
                target_gen = int(task.generation)
                for m in metas:
                    if not isinstance(m, dict):
                        unattributed += 1
                        continue
                    m_doc = m.get("document_id")
                    if m_doc == task.document_id:
                        # superseded 任务只对旧代次（gen < target）负责：
                        # 当前已发布代次的向量是清理的保护对象
                        if is_superseded and int(m.get("generation", 0)) >= target_gen:
                            continue
                        own_left += 1
                    elif m_doc:
                        pass  # 明确归属其它资源：不受本任务影响
                    else:
                        unattributed += 1
                if own_left:
                    raise RuntimeError(
                        f"本文档仍有 {own_left} 条向量残留（迟到写入，继续重试）"
                    )
                if unattributed:
                    raise UnresolvedCleanupError(
                        f"存在 {unattributed} 条无法归属的遗留向量（无 document_id 元数据），需映射处理"
                    )

                if await repo.mark_done(task.id, task.claim_token):
                    done += 1
            except WaitingWriterDeferred:
                # 挂起：不消耗重试预算、不退避，写入者关闭后立即可再核验
                if await repo.defer_for_writer(task.id, task.claim_token):
                    logger.info(f"【清理任务】等待写入者关闭: doc={task.document_id}")
            except UnresolvedCleanupError as e:
                if await repo.mark_blocked(task.id, task.claim_token, str(e)):
                    logger.warning(
                        f"【清理任务】归属不清进入 blocked（人工处理）: doc={task.document_id}, error={e}"
                    )
            except Exception as e:
                outcome = await repo.mark_failed(task.id, task.claim_token, str(e))
                logger.warning(
                    f"【清理任务】执行失败（{outcome}）: doc={task.document_id}, "
                    f"gen={task.generation}, reason={task.reason}, error={e}"
                )
        await session.commit()

    logger.info(f"【清理任务】本轮 {done}/{len(tasks)} 条核验完成")
    return done


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
