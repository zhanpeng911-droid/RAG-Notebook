import io

# -*- coding: utf-8 -*-
"""P1 生命周期恢复：租约、取消分支、提交结果判定、协调任务"""
import io


def patch(path, old, new, label):
    src = io.open(path, encoding="utf-8").read()
    assert old in src, f"NOT FOUND: {label}"
    src = src.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    print(f"OK: {label}")


# 1) 模型：租约字段
patch("app/models/index_execution.py",
      '''    closed_at = Column(DateTime(timezone=True), nullable=True, comment="关闭时间")''',
      '''    closed_at = Column(DateTime(timezone=True), nullable=True, comment="关闭时间")
    lease_expires_at = Column(DateTime(timezone=True), nullable=True,
                              comment="执行租约到期（失联判定依据；不证明远端停止）")''',
      "model lease column")

# 2) 执行仓库：登记时设租约；close 允许 writer 持令牌把 unknown 收敛为 closed；
#    协调把过期 writing 列为失联 unknown
patch("app/services/document_index_service.py",
      '''    async def open_execution(self, *, document_id: str, user_id: str,
                             generation: int, token: str) -> None:
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        self.session.add(DocumentIndexExecution(
            execution_id=str(uuid.uuid4()),
            document_id=document_id, user_id=user_id,
            generation=generation, token=token,
            status=ExecutionStatus.WRITING,
        ))
        await self.session.flush()''',
      '''    async def open_execution(self, *, document_id: str, user_id: str,
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
        await self.session.flush()''',
      "open sets lease")

patch("app/services/document_index_service.py",
      '''        result = await self.session.execute(
            update(DocumentIndexExecution)
            .where(DocumentIndexExecution.document_id == document_id)
            .where(DocumentIndexExecution.generation == generation)
            .where(DocumentIndexExecution.token == token)
            .where(DocumentIndexExecution.status == ExecutionStatus.WRITING)
            .values(status=status, closed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    close_reason=reason)
        )
        await self.session.flush()
        return result.rowcount > 0''',
      '''        # P1：writer 持令牌可把 writing 和 unknown（协调标记的失联）收敛为
        # closed——协调的 unknown 只是失联观测，不否定 writer 的结果证明；
        # 但不得无条件覆盖已确认的 closed（无该状态条件）。
        result = await self.session.execute(
            update(DocumentIndexExecution)
            .where(DocumentIndexExecution.document_id == document_id)
            .where(DocumentIndexExecution.generation == generation)
            .where(DocumentIndexExecution.token == token)
            .where(DocumentIndexExecution.status.in_([
                ExecutionStatus.WRITING, ExecutionStatus.UNKNOWN]))
            .values(status=status, closed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    close_reason=reason, lease_expires_at=None)
        )
        await self.session.flush()
        return result.rowcount > 0''',
      "close allows writer unknown convergence")

# 3) 协调：过期 writing → unknown（失联观测），公平有界
patch("app/services/document_index_service.py",
      '''    async def has_open_executions(self, document_id: str) -> bool:''',
      '''    async def mark_expired_writers_unknown(self, limit: int = 50) -> int:
        """P1 协调：租约过期的 writing 执行标记为失联 unknown（公平有界）。

        失联不证明远端停止——关联清理任务在执行真正关闭前不得 done；
        writer 持令牌仍可自行收敛。
        """
        from datetime import datetime, timezone
        from sqlalchemy import update
        from app.models.index_execution import DocumentIndexExecution, ExecutionStatus
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expired_ids = list((await self.session.execute(
            select(DocumentIndexExecution.execution_id)
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

    async def has_open_executions(self, document_id: str) -> bool:''',
      "coordinator marks lost writers")

# 4) 执行器：取消独立分支（unknown + 独立短会话 + 有界收尾后向上传播）
patch("app/services/document_index_service.py",
      '''        except Exception as e:
            from app.tasks.index_task import _sanitize_error_message''',
      '''        except asyncio.CancelledError:
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
            from app.tasks.index_task import _sanitize_error_message''',
      "cancel branch")

# 5) 发布提交结果判定：确认丢失时读持久事实，不误补偿
patch("app/services/document_index_service.py",
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
                # K1：发布成功即确定关闭写入者（同一事务）
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason="published")
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
                # K1：发布成功即确定关闭写入者（同一事务）
                await execution_repo.close_execution(
                    document_id=document_id, generation=generation,
                    token=execution_token, status="closed", reason="published")
            try:
                await session.commit()
            except Exception as commit_err:
                # P1：提交结果不明——用新会话读取持久事实判定，
                # 不盲目按失败补偿（可能已发布的新代次不得误删）
                commit_err_msg = str(commit_err)[:200]
                async with AsyncSessionLocal() as verify_session:
                    v_status = await repo_like_get_status(verify_session,
                                                          document_id, user_id)
                if v_status == DocumentIndexStatus.INDEXED:
                    logger.warning(
                        f"【文档索引】发布事务确认丢失但事实已提交: id={document_id}"
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
                        "retryable": True, "error": "publish_commit_uncertain"}''',
      "commit result determination")

# repo_like_get_status 辅助（新会话读状态，绕开 identity map）
patch("app/services/document_index_service.py",
      '''class _ExecutionRepository:''',
      '''async def repo_like_get_status(session, doc_id: str, user_id: str):
    """新会话读取文档状态（P1 提交结果判定用，绕开 identity map）。"""
    from sqlalchemy import select as _select
    from app.models.document_index import DocumentIndex
    result = await session.execute(
        _select(DocumentIndex.status).where(
            DocumentIndex.id == doc_id, DocumentIndex.user_id == user_id)
    )
    return result.scalar_one_or_none()


class _ExecutionRepository:''',
      "status probe helper")

print("PART1 DONE")
