"""
Agent 运行记录仓库 -- 封装 agent_runs、agent_steps、agent_feedback 表的 CRUD 操作。

SQL 注入防护：所有查询均通过 SQLAlchemy ORM 参数化执行，禁止拼接原始 SQL。
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentStep, AgentFeedback, AgentRunStatus
from app.core.logger_handler import logger


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

        if status in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED):
            values["completed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

        if values:
            await self.session.execute(
                update(AgentRun).where(AgentRun.id == run_id).values(**values)
            )
            await self.session.flush()

    async def get_run(self, run_id: str, user_id: str) -> Optional[AgentRun]:
        """获取运行记录"""
        result = await self.session.execute(
            select(AgentRun).where(
                and_(AgentRun.id == run_id, AgentRun.user_id == user_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_user_runs(
        self, user_id: str, session_id: str = None, limit: int = 20
    ) -> List[AgentRun]:
        """获取用户的运行记录"""
        conditions = [AgentRun.user_id == user_id]
        if session_id:
            conditions.append(AgentRun.session_id == session_id)

        result = await self.session.execute(
            select(AgentRun)
            .where(and_(*conditions))
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
