"""
Celery 异步任务队列 —— 替代 asyncio.create_task 的 fire-and-forget 模式。

使用 Redis 作为 broker，支持任务状态查询和异常重试。
主要任务：笔记标签自动生成（LLM 异步调用）。
"""
import os
from celery import Celery

from app.core.logger_handler import logger

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_DB = os.getenv("REDIS_DB", "0")

# 创建 Celery 实例
celery_app = Celery(
    "notebook",
    broker=f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}",
    backend=f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}",
)

# 显式注册任务模块 —— 确保 worker 启动时必定发现 index_task 中的任务。
# 这是比仅依赖 __init__.py 导入更可靠的方式，因为 Celery worker 启动时
# 会直接从 include 列表中导入模块，不依赖 Web 进程的 import 链。
celery_app.conf.include = [
    "app.tasks.index_task",
]

# Celery 配置
celery_app.conf.update(
    task_serializer="json",          # 任务序列化格式
    accept_content=["json"],         # 接受的内容类型
    result_serializer="json",        # 结果序列化格式
    timezone="Asia/Shanghai",        # 时区
    enable_utc=True,                 # 启用 UTC
    task_track_started=True,         # 追踪任务开始状态
    task_acks_late=True,             # 任务完成后才确认（防止 worker 崩溃丢失任务）
    worker_prefetch_multiplier=1,    # 每次只预取 1 个任务（避免长任务饿死）
)

# Celery Beat 定时任务调度 —— 定期扫描 pending_index 文档进行补偿
celery_app.conf.beat_schedule = {
    "batch-index-pending-every-5-minutes": {
        "task": "app.tasks.index_task.batch_index_pending_task",
        "schedule": 300.0,  # 每 5 分钟执行一次
        "args": (10,),       # 每次最多处理 10 个
    },
}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def generate_tags_task(self, note_id: str, user_id: str, content: str, llm_config: dict = None):
    """
    异步生成笔记标签和回顾记录。

    流程：
    1. 调用 LLM 分析笔记内容，生成标签和分类
    2. 创建回顾记录（艾宾浩斯遗忘曲线）

    失败时自动重试（最多 2 次，间隔 30 秒）。
    """
    import asyncio
    from app.services.note_service import note_service

    logger.info(f"【Celery】开始生成标签 note_id={note_id}")

    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                note_service._auto_tag_and_review(note_id, user_id, content, llm_config=llm_config)
            )
        finally:
            loop.close()

        logger.info(f"【Celery】标签生成完成 note_id={note_id}")
        return {"status": "completed", "note_id": note_id}
    except Exception as e:
        logger.error(f"【Celery】标签生成失败 note_id={note_id}: {e}")
        raise self.retry(exc=e)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True)
def sync_note_vector_task(self, note_id: str, user_id: str):
    """Fetch the newest MySQL note and synchronize Chroma outside the HTTP path."""
    import asyncio
    from app.db.db_config import AsyncSessionLocal
    from app.services.note_service import note_service

    async def _sync() -> None:
        async with AsyncSessionLocal() as session:
            note = await note_service.note_repo.get_by_id(session, note_id, user_id)
        if note is None:
            await asyncio.to_thread(note_service.note_index.delete_note, note_id, user_id)
            return
        await asyncio.to_thread(
            note_service.note_index.upsert_note,
            note.id,
            note.user_id,
            note.title,
            note.content,
        )

    try:
        asyncio.run(_sync())
        logger.info(f"Vector sync completed note_id={note_id}")
    except Exception as exc:
        logger.error(f"Vector sync failed note_id={note_id}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True)
def delete_note_vector_task(self, note_id: str, user_id: str):
    """Remove a note vector outside the HTTP path."""
    import asyncio
    from app.services.note_service import note_service

    try:
        asyncio.run(asyncio.to_thread(note_service.note_index.delete_note, note_id, user_id))
        logger.info(f"Vector delete completed note_id={note_id}")
    except Exception as exc:
        logger.error(f"Vector delete failed note_id={note_id}: {exc}")
        raise self.retry(exc=exc)
