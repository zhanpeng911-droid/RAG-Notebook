"""
Agent 运行记录仓库 -- 封装 agent_runs、agent_steps、agent_feedback 表的 CRUD 操作。

SQL 注入防护：所有查询均通过 SQLAlchemy ORM 参数化执行，禁止拼接原始 SQL。
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import select, update, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import (
    AgentRun,
    AgentStep,
    AgentFeedback,
    AgentRunStatus,
    TERMINAL_RUN_STATUSES,
)
from app.models.chat_history import ChatSession
from app.core.logger_handler import logger


def _exclude_deleted_session_runs():
    """运行可见性条件（S05）：session_id 为空运行始终可见；
    关联会话已删除（tombstone）的运行不可通过详情/列表/反馈读回。"""
    return or_(AgentRun.session_id.is_(None), ChatSession.deleted_at.is_(None))


class AgentRunRepository:
    """Agent 运行记录仓库"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_run(
        self,
        user_id: str,
        query: str,
        session_id: str = None,
        space_id: str = None,
        model_config: dict = None,
    ) -> AgentRun:
        """创建运行记录"""
        run = AgentRun(
            id=str(uuid.uuid4()),
            user_id=user_id,
            session_id=session_id,
            space_id=space_id,
            query=query,
            status=AgentRunStatus.STARTED,
            model_config=model_config,
        )
        self.session.add(run)
        await self.session.flush()
        logger.info(f"【Agent记录】创建运行记录: id={run.id}")
        return run

    async def update_run(
        self,
        run_id: str,
        status: str = None,
        answer: str = None,
        query_type: str = None,
        error_message: str = None,
        retrieval_rounds: int = None,
        evidence_count: int = None,
        citation_count: int = None,
        total_time_ms: int = None,
    ) -> None:
        """更新运行记录"""
        values = {}
        if status is not None:
            values["status"] = status
        if answer is not None:
            values["answer"] = answer
        if query_type is not None:
            values["query_type"] = query_type
        if error_message is not None:
            values["error_message"] = error_message
        if retrieval_rounds is not None:
            values["retrieval_rounds"] = retrieval_rounds
        if evidence_count is not None:
            values["evidence_count"] = evidence_count
        if citation_count is not None:
            values["citation_count"] = citation_count
        if total_time_ms is not None:
            values["total_time_ms"] = total_time_ms

        if status in TERMINAL_RUN_STATUSES:
            values["completed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

        if values:
            await self.session.execute(
                update(AgentRun).where(AgentRun.id == run_id).values(**values)
            )
            await self.session.flush()

    async def update_run_terminal(
        self,
        run_id: str,
        status: str,
        answer: str = None,
        error_message: str = None,
        citation_count: int = None,
        total_time_ms: int = None,
    ) -> bool:
        """条件写终态：只有运行尚未进入终态时才写入，防止迟到的取消/超时
        覆盖已完成的运行结果（进程崩溃后由恢复任务保证能收敛到终态）。"""
        terminal_values = tuple(s.value for s in TERMINAL_RUN_STATUSES)
        values = {
            "status": status,
            "completed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        if answer is not None:
            values["answer"] = answer
        if error_message is not None:
            values["error_message"] = error_message
        if citation_count is not None:
            values["citation_count"] = citation_count
        if total_time_ms is not None:
            values["total_time_ms"] = total_time_ms

        result = await self.session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .where(AgentRun.status.not_in(terminal_values))
            .values(**values)
        )
        await self.session.flush()
        return result.rowcount > 0

    async def get_run(self, run_id: str, user_id: str) -> Optional[AgentRun]:
        """获取运行记录（关联会话已删除的运行不可读，S05）"""
        result = await self.session.execute(
            select(AgentRun)
            .outerjoin(ChatSession, AgentRun.session_id == ChatSession.id)
            .where(
                and_(AgentRun.id == run_id, AgentRun.user_id == user_id),
                _exclude_deleted_session_runs(),
            )
        )
        return result.scalar_one_or_none()

    async def get_user_runs(
        self, user_id: str, session_id: str = None, limit: int = 20
    ) -> List[AgentRun]:
        """获取用户的运行记录（排除关联已删除会话的运行，S05）"""
        conditions = [AgentRun.user_id == user_id]
        if session_id:
            conditions.append(AgentRun.session_id == session_id)

        result = await self.session.execute(
            select(AgentRun)
            .outerjoin(ChatSession, AgentRun.session_id == ChatSession.id)
            .where(and_(*conditions), _exclude_deleted_session_runs())
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def add_step(
        self,
        run_id: str,
        user_id: str,
        phase: str,
        step_data: dict = None,
        duration_ms: int = None,
    ) -> None:
        """添加执行步骤"""
        step = AgentStep(
            run_id=run_id,
            user_id=user_id,
            phase=phase,
            step_data=step_data,
            duration_ms=duration_ms,
        )
        self.session.add(step)
        await self.session.flush()

    async def add_feedback(
        self,
        run_id: str,
        user_id: str,
        rating: int,
        comment: str = None,
    ) -> AgentFeedback:
        """添加反馈"""
        feedback = AgentFeedback(
            run_id=run_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
        )
        self.session.add(feedback)
        await self.session.flush()
        logger.info(f"【Agent记录】添加反馈: run_id={run_id}, rating={rating}")
        return feedback

    async def get_run_steps(self, run_id: str) -> List[AgentStep]:
        """获取运行步骤"""
        result = await self.session.execute(
            select(AgentStep)
            .where(AgentStep.run_id == run_id)
            .order_by(AgentStep.created_at.asc())
        )
        return list(result.scalars().all())

    async def touch_run_heartbeat(self, run_id: str) -> None:
        """更新运行心跳（R6C）：流式执行中定期调用，作为 lease 依据。"""
        await self.session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .where(AgentRun.status == AgentRunStatus.STARTED.value)
            .values(heartbeat_at=datetime.now(timezone.utc).replace(tzinfo=None))
        )
        await self.session.flush()

    async def reclaim_stale_runs(self, cutoff: datetime, limit: int = 100) -> int:
        """回收过期运行（R6C）：将超过 lease 仍处于 started 的运行条件置为 timeout。

        lease 依据 = COALESCE(heartbeat_at, created_at)：流式执行中心跳定期
        更新，进程崩溃后心跳停止，超过 cutoff 的孤儿运行被回收；
        正常运行生命周期只有数十秒（Guardrails.MAX_TOTAL_TIME=45s）。
        limit 参数实际生效（子查询限量，按创建时间最旧的先回收）；
        仅更新仍为 started 的行，不触碰已有终态的运行。
        """
        lease_expr = func.coalesce(AgentRun.heartbeat_at, AgentRun.created_at)
        stale_ids = (
            select(AgentRun.id)
            .where(AgentRun.status == AgentRunStatus.STARTED.value)
            .where(lease_expr < cutoff)
            .order_by(AgentRun.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(
            update(AgentRun)
            .where(AgentRun.id.in_(stale_ids))
            .values(
                status=AgentRunStatus.TIMEOUT.value,
                error_message="运行超时未更新，已被回收任务置为 timeout",
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await self.session.flush()
        return result.rowcount
