"""
服务层 —— 提供业务逻辑封装。

主要服务：
- note_service: 笔记服务（CRUD + 向量双写 + 异步标签）
- review_service: 回顾服务（艾宾浩斯遗忘曲线）
- session_manager: 会话管理器（对话历史持久化）

S01：代理不在导入期快照 database_session_manager（彼时恒为 None，
初始化后也不会更新），而是每次通过 get_session_manager() 读取定义
模块的当前实例；未初始化时明确失败。
这里刻意不把 `database_session_manager` 变量导入包命名空间 ——
该名字与子模块同名，导入会遮蔽包属性上的子模块对象。
"""
from app.services.database_session_manager import (
    DatabaseSessionManager,
    get_session_manager,
    init_database_session_manager,
)


class SessionManagerProxy:
    """会话管理器代理 —— 每次读取当前实例（不快照导入时的绑定）"""
    @property
    def session_manager(self):
        return get_session_manager()


session_manager = SessionManagerProxy()

__all__ = [
    "session_manager",
    "DatabaseSessionManager",
    "get_session_manager",
    "init_database_session_manager",
]
