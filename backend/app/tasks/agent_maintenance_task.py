"""
Agent 运行维护任务 —— 由 Celery Beat 定时执行（R6C）。

回收长时间停留在 started 状态的孤儿运行：进程崩溃/断电后
update_run_terminal 永远不会执行，这些运行若不回收会永久显示 running。
正常生命周期只有数十秒（Guardrails.MAX_TOTAL_TIME=45s），
默认 15 分钟无更新即回收为 timeout（条件写，不触碰已有终态）。
"""
import asyncio
from datetime import datetime, timedelta, timezone

from app.tasks.celery_app import celery_app
from app.core.logger_handler import logger

# started 运行的最长容忍时长（分钟）：远大于单次执行上限，避免误回收慢请求
STALE_RUN_MAX_AGE_MINUTES = 15


def _run_async(coro):
    """在同步 Celery worker 中运行异步代码"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(ignore_result=True)
def reclaim_stale_agent_runs_task(max_age_minutes: int = STALE_RUN_MAX_AGE_MINUTES):
    """回收过期运行：started 且创建时间早于 cutoff 的运行置为 timeout。"""
    _run_async(_reclaim_stale_async(max_age_minutes))


async def _reclaim_stale_async(max_age_minutes: int) -> int:
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.agent_run_repository import AgentRunRepository

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=max_age_minutes
    )
    async with AsyncSessionLocal() as session:
        repo = AgentRunRepository(session)
        reclaimed = await repo.reclaim_stale_runs(cutoff)
        await session.commit()

    if reclaimed:
        logger.warning(f"【运行维护】已回收 {reclaimed} 条过期运行（>{max_age_minutes} 分钟无更新）")
    return reclaimed
