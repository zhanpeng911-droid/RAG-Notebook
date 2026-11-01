"""知识洞察 Celery 任务入口。"""
from app.tasks.celery_app import celery_app
from app.core.logger_handler import logger


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


@celery_app.task(ignore_result=True)
def dispatch_queued_insight_jobs_task(limit: int = 50):
    """Retry the commit-to-dispatch gap; duplicate deliveries are safe to claim."""
    from app.tasks.index_task import _run_async
    _run_async(_dispatch_queued_jobs(limit))


async def _dispatch_queued_jobs(limit: int) -> int:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select, update

    from app.db import db_config
    from app.models.knowledge_insight import JobStatus, KnowledgeGenerationJob

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=1)
    async with db_config.AsyncSessionLocal() as session:
        # A job stays queued until a worker atomically claims it. Ordering by
        # the last compensation attempt (not only updated_at) rotates the
        # oldest batch instead of starving everything after the first N rows.
        ids = list((await session.execute(
            select(KnowledgeGenerationJob.id).where(
                KnowledgeGenerationJob.status == JobStatus.QUEUED,
                KnowledgeGenerationJob.updated_at < cutoff,
            ).order_by(
                func.coalesce(KnowledgeGenerationJob.last_dispatch_at,
                              KnowledgeGenerationJob.updated_at),
                KnowledgeGenerationJob.id,
            ).limit(limit)
        )).scalars().all())
    sent = 0
    for job_id in ids:
        try:
            generate_insight_task.delay(job_id)
            sent += 1
        except Exception as exc:
            logger.warning("【知识洞察】补偿投递失败 job=%s: %s", job_id, type(exc).__name__)
        finally:
            # Advance the cursor for success and failure alike: a broker error
            # must not pin the same row at the head of every Beat tick. The
            # conditional update cannot resurrect a job another worker claimed.
            try:
                async with db_config.AsyncSessionLocal() as session:
                    await session.execute(
                        update(KnowledgeGenerationJob).where(
                            KnowledgeGenerationJob.id == job_id,
                            KnowledgeGenerationJob.status == JobStatus.QUEUED,
                        ).values(last_dispatch_at=now)
                    )
                    await session.commit()
            except Exception as exc:
                logger.warning("【知识洞察】补偿游标更新失败 job=%s: %s",
                               job_id, type(exc).__name__)
    return sent


async def _reclaim_stale_jobs(limit: int) -> int:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select, update

    from app.db import db_config
    from app.models.knowledge_insight import JobStatus, KnowledgeGenerationJob
    from app.services.insight_generation_service import (
        INSIGHT_EXECUTION_BUDGET_SECONDS, INSIGHT_RECLAIM_GRACE_SECONDS,
    )

    # The execution timeout starts just after the claim, but never before the
    # claim commit. The grace prevents reclaiming a call that is still inside
    # its own budget while the worker is finishing setup or publishing.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=INSIGHT_EXECUTION_BUDGET_SECONDS + INSIGHT_RECLAIM_GRACE_SECONDS)
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
                KnowledgeGenerationJob.started_at < cutoff,
            ).values(
                status=JobStatus.FAILED,
                error="execution_timeout",
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
