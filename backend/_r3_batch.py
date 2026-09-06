import io

# -*- coding: utf-8 -*-
"""评审第三批修复：T01 重构、T09 blocked、领取复核、删重复实现"""


def patch(path, old, new, label):
    src = io.open(path, encoding="utf-8").read()
    assert old in src, f"NOT FOUND: {label}"
    src = src.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    print(f"OK: {label}")


# ---- A. 移除两个 xfail ----
patch("tests/test_closure_rework.py",
      '@pytest.mark.xfail(reason="K1 done 门槛在清理 worker 未生效（N1 待接线修复），保持红色登记", strict=False)\nasync def test_t01',
      'async def test_t01', "remove xfail t01")
patch("tests/test_closure_rework.py",
      '@pytest.mark.xfail(reason="document_deleted 遗留向量归属判断未接入 blocked 语义（N4 待实施）", strict=False)\nasync def test_t09',
      'async def test_t09', "remove xfail t09")

# ---- B. T01 重构：显式 writing 执行 + 生产关闭接口 ----
patch("tests/test_closure_rework.py",
      '''    # 旧写入者（持有旧代次）在两次核验之间写入迟到数据
    def late_write():
        env.vs.add_documents(
            [NS(page_content="迟到", metadata={"user_id": "u1", "md5": md5,
                                              "document_id": doc_id,
                                              "generation": 1})],
            ids=[f"u1:{doc_id}:g1:late"],
        )

    # 第一次 worker：核验通过 → 不 done（首空登记/宽限）
    await _run_cleanup_worker_once(env)
    # 迟到写入发生在首空之后
    late_write()
    # 第二次 worker：迟到数据必须被捕获，不得 done
    await _run_cleanup_worker_once(env)
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status != "done", "迟到写入后任务不得标完成"

    # 写入者关闭（execution closed）后：清理最终收敛
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)''',
      '''    # 显式创建 writing 执行并提交（模拟删除前已领取、删除后仍在写）
    from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
    from app.services.document_index_service import _ExecutionRepository
    async with env.factory() as s:
        s.add(DocumentIndexExecution(
            execution_id="exec-late", document_id=doc_id, user_id="u1",
            generation=1, token="tok-late", status=ExecutionStatus.WRITING))
        await s.commit()
    # 断言前置状态：数据库确实存在 writing 执行
    async with env.factory() as s:
        e = (await s.execute(select(DocumentIndexExecution)
                             .where(DocumentIndexExecution.execution_id == "exec-late"))).scalar_one()
        assert e.status == ExecutionStatus.WRITING

    def late_write():
        env.vs.add_documents(
            [NS(page_content="迟到", metadata={"user_id": "u1", "md5": md5,
                                              "document_id": doc_id,
                                              "generation": 1})],
            ids=[f"u1:{doc_id}:g1:late"],
        )

    # 第一次 worker：写入者未关闭 → 不得 done
    await _run_cleanup_worker_once(env)
    late_write()
    # 第二次 worker：仍未关闭 → 不得 done
    await _run_cleanup_worker_once(env)
    tasks = await _cleanup_tasks(env)
    task = next(t for t in tasks if t.reason == "document_deleted")
    assert task.status != "done", "写入者未关闭时任务不得标完成"

    # 经生产关闭接口提交 closed（不直接改任务状态）
    async with env.factory() as s:
        ok = await _ExecutionRepository(s).close_execution(
            document_id=doc_id, generation=1, token="tok-late",
            status="closed", reason="writer finished")
        assert ok is True
    # 写入者关闭后：清理最终收敛（迟到向量被精确清除）
    await _run_cleanup_worker_once(env)
    await _run_cleanup_worker_once(env)''',
      "T01 rewrite")

# ---- C. T09 blocked 语义 + 领取 UPDATE 复核 + 删重复实现 ----
patch("app/repositories/cleanup_task_repository.py",
      '''    async def has_open_executions(self, document_id: str) -> bool:
        """K1：该文档是否存在未关闭（writing/unknown）的执行——
        写入者未确定关闭前，清理任务不得判 done。"""
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        result = await self.session.execute(
            select(DocumentIndexExecution.execution_id).where(
                DocumentIndexExecution.document_id == document_id,
                DocumentIndexExecution.status != ExecutionStatus.CLOSED,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

''',
      '''    async def mark_blocked(self, task_id: str, token: str, reason: str) -> bool:
        """归属不清等不可自动恢复情形：进入 blocked（人工可见），不消耗重试预算。"""
        result = await self.session.execute(
            update(DocumentCleanupTask)
            .where(DocumentCleanupTask.id == task_id)
            .where(DocumentCleanupTask.claim_token == token)
            .where(DocumentCleanupTask.status == CleanupTaskStatus.CLAIMED)
            .values(status=CleanupTaskStatus.BLOCKED, last_error=reason[:500],
                    updated_at=_now())
        )
        return result.rowcount > 0

''',
      "remove duplicate impl, add mark_blocked")

patch("app/repositories/cleanup_task_repository.py",
      '''            .where(DocumentCleanupTask.status.in_([
                CleanupTaskStatus.PENDING, CleanupTaskStatus.FAILED,
                CleanupTaskStatus.CLAIMED,
            ]))
            .values(status=CleanupTaskStatus.CLAIMED, claim_token=token,
                    lease_expires_at=lease, updated_at=now)''',
      '''            .where(DocumentCleanupTask.status.in_([
                CleanupTaskStatus.PENDING, CleanupTaskStatus.FAILED,
                CleanupTaskStatus.CLAIMED,
            ]))
            .where((DocumentCleanupTask.next_retry_at.is_(None))
                   | (DocumentCleanupTask.next_retry_at <= now))
            .where((DocumentCleanupTask.status != CleanupTaskStatus.CLAIMED)
                   | (DocumentCleanupTask.lease_expires_at.is_(None))
                   | (DocumentCleanupTask.lease_expires_at <= now))
            .values(status=CleanupTaskStatus.CLAIMED, claim_token=token,
                    lease_expires_at=lease, updated_at=now)''',
      "claim update recheck lease/next_retry")

# worker：unattributed 遗留 → blocked；删除分支重构
patch("app/tasks/index_task.py",
      '''                    if other_doc is None:
                        await _aio.to_thread(
                            store.vectors_store.delete,
                            where={"$and": [{"user_id": task.user_id}, {"md5": task.md5}]},
                        )
                    # 核验：文档范围必须为空；md5 范围仅在其他文档不存在时要求为空
                    remain_doc = await _aio.to_thread(
                        store.vectors_store.get,
                        where={"$and": [
                            {"user_id": task.user_id},
                            {"document_id": task.document_id},
                        ]},
                        include=[],
                    )
                    residual = len(remain_doc.get("ids", [])) if isinstance(remain_doc, dict) else 1
                    if other_doc is None:
                        remain_md5 = await _aio.to_thread(
                            store.vectors_store.get,
                            where={"$and": [{"user_id": task.user_id}, {"md5": task.md5}]},
                            include=[],
                        )
                        residual += len(remain_md5.get("ids", [])) if isinstance(remain_md5, dict) else 0
                    if residual:
                        raise RuntimeError(
                            f"删除后仍有 {residual} 条向量残留（迟到写入或遗留格式，继续重试）"
                        )''',
      '''                    if other_doc is None:
                        await _aio.to_thread(
                            store.vectors_store.delete,
                            where={"$and": [{"user_id": task.user_id}, {"md5": task.md5}]},
                        )
                    # 核验：文档范围必须为空
                    remain_doc = await _aio.to_thread(
                        store.vectors_store.get,
                        where={"$and": [
                            {"user_id": task.user_id},
                            {"document_id": task.document_id},
                        ]},
                        include=[],
                    )
                    residual = len(remain_doc.get("ids", [])) if isinstance(remain_doc, dict) else 1
                    # 评审 T09：同 MD5 存在其它文档时，无法归属的遗留向量
                    # 进入 blocked（人工/映射处理），不得 md5 盲删、不得 done
                    if other_doc is not None:
                        remain_all = await _aio.to_thread(
                            store.vectors_store.get,
                            where={"$and": [{"user_id": task.user_id}, {"md5": task.md5}]},
                            include=[],
                        )
                        doc_scoped = set(remain_doc.get("ids", [])) if isinstance(remain_doc, dict) else set()
                        all_ids = set(remain_all.get("ids", [])) if isinstance(remain_all, dict) else set()
                        if all_ids - doc_scoped:
                            raise UnresolvedCleanupError(
                                f"存在 {len(all_ids - doc_scoped)} 条无法归属的遗留向量（同 MD5 有其它文档），需映射处理"
                            )
                    if residual:
                        raise RuntimeError(
                            f"删除后仍有 {residual} 条向量残留（迟到写入或遗留格式，继续重试）"
                        )''',
      "T09 unattributed detection")

# worker except：UnresolvedCleanupError → blocked（不耗重试预算）
patch("app/tasks/index_task.py",
      '''            except Exception as e:
                outcome = await repo.mark_failed(task.id, task.claim_token, str(e))''',
      '''            except UnresolvedCleanupError as e:
                if await repo.mark_blocked(task.id, task.claim_token, str(e)):
                    logger.warning(
                        f"【清理任务】归属不清进入 blocked（人工处理）: doc={task.document_id}, error={e}"
                    )
            except Exception as e:
                outcome = await repo.mark_failed(task.id, task.claim_token, str(e))''',
      "worker blocked branch")

# UnresolvedCleanupError 定义 + worker 引用
patch("app/repositories/cleanup_task_repository.py",
      '''CLAIM_LEASE_MINUTES = 10
MAX_ATTEMPTS = 5''',
      '''CLAIM_LEASE_MINUTES = 10
MAX_ATTEMPTS = 5


class UnresolvedCleanupError(Exception):
    """清理目标无法精确归属（遗留向量/映射缺失）——进入 blocked，不重试。"""''',
      "error class")
patch("app/tasks/index_task.py",
      '''    from app.repositories.cleanup_task_repository import CleanupTaskRepository

    done = 0''',
      '''    from app.repositories.cleanup_task_repository import (
        CleanupTaskRepository, UnresolvedCleanupError,
    )

    done = 0''',
      "worker imports error class")

print("ALL PATCHES DONE")
