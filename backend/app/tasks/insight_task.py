"""知识洞察 Celery 任务入口。"""
from app.tasks.celery_app import celery_app


@celery_app.task(bind=True, max_retries=0, ignore_result=True)
def generate_insight_task(self, job_id: str):
    """执行一个已持久化的洞察任务。

    任务状态由 API 提交后写入；worker 只领取 queued 任务，
    重复投递不会并行执行。失败由服务层落 failed，用户可显式重试。
    """
    from app.services.insight_generation_service import claim_and_run_job
    from app.tasks.index_task import _run_async

    _run_async(claim_and_run_job(job_id))


@celery_app.task(ignore_result=True)
def reclaim_stale_insight_jobs_task(limit: int = 50):
    """回收长时间停留在 running 的洞察任务（worker 崩溃/重启）。

    只改变任务状态，不删除产物；用户可显式重新生成。
    """
    from app.tasks.index_task import _run_async
    _run_async(_reclaim_stale_jobs(limit))


async def _reclaim_stale_jobs(limit: int) -> int:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select, update

    from app.db import db_config
    from app.models.knowledge_insight import JobStatus, KnowledgeGenerationJob

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    async with db_config.AsyncSessionLocal() as session:
        stale_ids = list((await session.execute(
            select(KnowledgeGenerationJob.id).where(
                KnowledgeGenerationJob.status == JobStatus.RUNNING,
                KnowledgeGenerationJob.started_at < cutoff,
            ).order_by(KnowledgeGenerationJob.started_at).limit(limit)
        )).scalars().all())
        if not stale_ids:
            return 0
        result = await session.execute(
            update(KnowledgeGenerationJob).where(
                KnowledgeGenerationJob.id.in_(stale_ids),
                KnowledgeGenerationJob.status == JobStatus.RUNNING,
            ).values(
                status=JobStatus.FAILED,
                error="execution_timeout",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
