"""
服务层 —— 提供业务逻辑封装。

主要服务：
- note_service: 笔记服务（CRUD + 向量双写 + 异步标签）
- review_service: 回顾服务（艾宾浩斯遗忘曲线）
- session_manager: 会话管理器（对话历史持久化）
"""
from app.services.database_session_manager import DatabaseSessionManager, database_session_manager


class SessionManagerProxy:
    """会话管理器代理 —— 延迟加载，确保 database_session_manager 已初始化"""
    @property
    def session_manager(self):
        return database_session_manager


session_manager = SessionManagerProxy()

__all__ = ["session_manager", "DatabaseSessionManager"]
