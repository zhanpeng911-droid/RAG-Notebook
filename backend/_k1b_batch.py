import io

# -*- coding: utf-8 -*-
"""K1 接入：执行清单登记/关闭接入执行器与 v1；模型注册；v1 ID 统一"""


def patch(path, old, new, label):
    src = io.open(path, encoding="utf-8").read()
    assert old in src, f"NOT FOUND: {label}"
    src = src.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    print(f"OK: {label}")


# 模型注册
patch(
    "app/models/__init__.py",
    "from app.models.cleanup_task import DocumentCleanupTask, CleanupTaskStatus",
    """from app.models.cleanup_task import DocumentCleanupTask, CleanupTaskStatus
from app.models.index_execution import DocumentIndexExecution, ExecutionStatus""",
    "model import",
)
patch(
    "app/models/__init__.py",
    '''    "DocumentCleanupTask", "CleanupTaskStatus",
]''',
    '''    "DocumentCleanupTask", "CleanupTaskStatus",
    "DocumentIndexExecution", "ExecutionStatus",
]''',
    "model all",
)

# 执行器：claim 事务登记 writing 执行
patch(
    "app/services/document_index_service.py",
    '''            # 原子抢占 INDEXING（仅 pending_index 可进入），同时取得执行代次
            generation = await repo.claim_for_indexing(document_id)
            if generation is None:
                logger.info(f"【文档索引】未抢占到索引权（可能已在执行或删除中），跳过: id={document_id}")
                current = await repo.get_status(document_id, user_id)
                await session.commit()
                return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": None}
            await session.commit()''',
    '''            # 原子抢占 INDEXING（仅 pending_index 可进入），同时取得执行代次
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
            await session.commit()''',
    "executor opens execution",
)

# 执行器：发布事务关闭执行（closed）
patch(
    "app/services/document_index_service.py",
    '''            # P0/C4：发布指针与旧代次清理任务在同一事务提交——
            # 提交失败两者皆回滚（旧向量原样保留，文档保持旧代次可检索）；
            # 提交成功后清理任务进入持久队列，由公平领取的 worker 执行
            from app.models.cleanup_task import DocumentCleanupTask
            from app.repositories.cleanup_task_repository import CleanupTaskRepository
            cleanup_repo = CleanupTaskRepository(session)
            async with session.begin_nested():
                await cleanup_repo.create_task(
                    document_id=document_id, user_id=user_id, md5=doc.md5,
                    generation=generation, reason="superseded_generation",
                    legacy_chunk_count=legacy_chunk_count,
                )
            await session.commit()''',
    '''            # P0/C4/K1：发布指针 + 关闭执行 + 旧代次清理任务同一事务提交——
            # 提交失败三者皆回滚（旧向量原样保留，文档保持旧代次可检索）；
            # 提交成功后清理任务进入持久队列，由公平领取的 worker 执行
            from app.repositories.cleanup_task_repository import CleanupTaskRepository
            cleanup_repo = CleanupTaskRepository(session)
            async with session.begin_nested():
                await cleanup_repo.create_task(
                    document_id=document_id, user_id=user_id, md5=doc.md5,
                    generation=generation, reason="superseded_generation",
                    legacy_chunk_count=legacy_chunk_count,
                )
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed",
                    reason="published")
            await session.commit()''',
    "publish closes execution",
)

# 执行器：确定性失败（空文档/切片）关闭为 closed；异常路径 unknown
patch(
    "app/services/document_index_service.py",
    '''            # 检查文件是否存在（此时必然处于 indexing，失败写回由本执行方持有）
            if not os.path.exists(doc.file_path):
                error_msg = f"文件已丢失: {doc.file_path}"
                logger.error(f"【文档索引】{error_msg}, id={document_id}")
                await repo.mark_index_failed_if_indexing(
                    document_id, error_msg, generation=generation)
                await session.commit()
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": error_msg}''',
    '''            # 检查文件是否存在（此时必然处于 indexing，失败写回由本执行方持有）
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
                        "retryable": False, "error": error_msg}''',
    "missing file closes execution",
)

patch(
    "app/services/document_index_service.py",
    '''        except Exception as e:
            from app.tasks.index_task import _sanitize_error_message
            error_msg = _sanitize_error_message(str(e))
            logger.error(f"【文档索引】索引执行失败: id={document_id}, error={error_msg}")
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
                        "retryable": True, "error": error_msg[:200]}''',
    '''        except Exception as e:
            from app.tasks.index_task import _sanitize_error_message
            error_msg = _sanitize_error_message(str(e))
            logger.error(f"【文档索引】索引执行失败: id={document_id}, error={error_msg}")
            # K1：异常路径结果不确定（远端可能已写入）→ 执行保持 unknown，
            # 由清理任务的"写入者已关闭"门槛阻止最终完成
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
                        "retryable": True, "error": error_msg[:200]}''',
    "exception marks unknown",
)

# 执行器：空文档/空切片分支也关闭（确定性失败）
patch(
    "app/services/document_index_service.py",
    '''            if not documents:
                if not await repo.mark_index_failed_if_indexing(
                    document_id, "文档加载为空", generation=generation
                ):
                    current = await repo.get_status(document_id, user_id)
                    await session.commit()
                    return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                            "retryable": False, "error": None}
                await session.commit()
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": "文档加载为空"}''',
    '''            if not documents:
                if not await repo.mark_index_failed_if_indexing(
                    document_id, "文档加载为空", generation=generation
                ):
                    current = await repo.get_status(document_id, user_id)
                    await session.commit()
                    return {"status": current or DocumentIndexStatus.INDEX_FAILED,
                            "retryable": False, "error": None}
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason="文档加载为空")
                await session.commit()
                return {"status": DocumentIndexStatus.INDEX_FAILED,
                        "retryable": False, "error": "文档加载为空"}''',
    "empty docs closes execution",
)

# _ExecutionRepository 轻量实现 + uuid 导入确认
patch(
    "app/services/document_index_service.py",
    '''async def execute_index_document(document_id: str, user_id: str) -> dict:''',
    '''class _ExecutionRepository:
    """执行清单仓库（K1）——登记/关闭/查询未关闭执行。"""

    def __init__(self, session):
        self.session = session

    async def open_execution(self, *, document_id: str, user_id: str,
                             generation: int, token: str) -> None:
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        self.session.add(DocumentIndexExecution(
            execution_id=str(uuid.uuid4()),
            document_id=document_id, user_id=user_id,
            generation=generation, token=token,
            status=ExecutionStatus.WRITING,
        ))
        await self.session.flush()

    async def close_execution(self, *, document_id: str, generation: int,
                              token: str, status: str, reason: str) -> bool:
        from datetime import datetime, timezone
        from sqlalchemy import update
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        result = await self.session.execute(
            update(DocumentIndexExecution)
            .where(DocumentIndexExecution.document_id == document_id)
            .where(DocumentIndexExecution.generation == generation)
            .where(DocumentIndexExecution.token == token)
            .where(DocumentIndexExecution.status == ExecutionStatus.WRITING)
            .values(status=status, closed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    close_reason=reason)
        )
        await self.session.flush()
        return result.rowcount > 0

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


async def execute_index_document(document_id: str, user_id: str) -> dict:''',
    "execution repo class",
)

# 清理 worker 的 open-execution 检查复用同一实现
patch(
    "app/tasks/index_task.py",
    '''                # K1：写入者未确定关闭（writing/unknown）→ 不得判 done。
                # 查空只是观测（verified_empty_at 保留为观测字段）；
                # 关闭证明来自执行清单。关闭 + 核验为空 → done。
                if await repo.has_open_executions(task.document_id):
                    raise RuntimeError("存在未关闭的写入执行（waiting_writer），暂缓完成")''',
    '''                # K1：写入者未确定关闭（writing/unknown）→ 不得判 done。
                # 查空只是观测（verified_empty_at 保留为观测字段）；
                # 关闭证明来自执行清单。关闭 + 核验为空 → done。
                from app.services.document_index_service import _ExecutionRepository
                exec_repo = _ExecutionRepository(session)
                if await exec_repo.has_open_executions(task.document_id):
                    raise RuntimeError("存在未关闭的写入执行（waiting_writer），暂缓完成")''',
    "worker uses execution repo",
)

# v1 ID 统一：kb: 前缀 → 与 v2 相同的 {user}:{document_id}:g{n}:{i}
patch(
    "app/rag/document_handler/processor.py",
    '''                    stable_ids = [
                        f"kb:{user_id}:{md5_hex}:g{int(generation)}:{i}"
                        for i in range(len(document))
                    ]''',
    '''                    stable_ids = [
                        f"{user_id}:{document_id}:g{int(generation)}:{i}"
                        for i in range(len(document))
                    ]''',
    "v1 unified ids",
)

print("ALL K1 PATCHES DONE")
