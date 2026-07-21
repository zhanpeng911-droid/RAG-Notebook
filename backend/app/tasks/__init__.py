"""
Celery 任务注册 —— 确保 worker 启动时所有任务都被发现。

必须在这里显式导入所有任务模块，否则 celery -A app.tasks.celery_app worker
启动时不会自动发现 index_task 中定义的任务。
"""
from app.tasks.celery_app import celery_app  # noqa: F401
from app.tasks.index_task import index_document_task, batch_index_pending_task  # noqa: F401

__all__ = [
    "celery_app",
    "index_document_task",
    "batch_index_pending_task",
]
