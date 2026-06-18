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
    "rag_notebook",
    broker=f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}",
    backend=f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}",
)

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
