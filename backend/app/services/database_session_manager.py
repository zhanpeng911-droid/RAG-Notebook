"""
数据库会话管理器 —— 基于 MySQL 的对话历史持久化。

功能：
- 获取会话历史（验证用户权限）
- 添加消息（自动创建会话）
- 删除会话（级联删除消息）
- 获取用户所有会话

数据模型：
- ChatSession: 会话表（id, user_id, title）
- ChatMessage: 消息表（session_id, role, content）
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

from sqlalchemy import delete, select

from app.db.db_config import AsyncSessionLocal
from app.models.chat_history import ChatSession, ChatMessage
from app.models.agent_run import AgentFeedback, AgentRun, AgentStep
from app.core.logger_handler import logger


class DatabaseSessionManager:
    """基于数据库的会话管理器 —— 负责对话历史的 CRUD"""

    def __init__(self):
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls) -> "DatabaseSessionManager":
        """
        异步创建并初始化 DatabaseSessionManager
        :return: 初始化完成的 DatabaseSessionManager 实例
        """
        instance = cls()
        logger.info("【数据库会话管理】初始化完成")
        return instance

    async def get_session(self, session_id: str, user_id: str) -> Dict:
        """获取会话（已删除的 tombstone 视为不存在）"""
        async with AsyncSessionLocal() as db:
            # 尝试查找会话，验证属于该用户且未删除
            result = await db.run_sync(
                lambda session: session.query(ChatSession).filter(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.deleted_at.is_(None),
                ).first()
            )

            if result:
                # 获取会话历史
                messages = await db.run_sync(
                    lambda session: session.query(ChatMessage).filter(ChatMessage.session_id == result.id).order_by(ChatMessage.created_at).all()
                )
                # 转换为 (user_message, assistant_message) 格式
                history = []
                i = 0
                while i < len(messages):
                    if messages[i].role == "user" and i + 1 < len(messages) and messages[i+1].role == "assistant":
                        history.append((messages[i].content, messages[i+1].content))
                        i += 2
                    else:
                        i += 1
                return {"history": history}
            else:
                existing_session = await db.run_sync(
                    lambda session: session.query(ChatSession).filter(ChatSession.id == session_id).first()
                )
                from fastapi import HTTPException, status
                if existing_session:
                    if existing_session.deleted_at is not None:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail="会话不存在或已删除",
                        )
                    logger.warning(f"【数据库会话管理】会话 {session_id} 不属于用户 {user_id}")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="当前会话不属于你",
                    )
                # GET 只负责读取；不存在的会话不能被读取请求静默创建。
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="会话不存在",
                )


    async def add_message(self, session_id: str, user_id: str, user_message: str, assistant_message: str):
        """向已有会话追加消息。

        会话必须存在且属于当前用户、未被删除；不再替迟到请求自动创建旧会话 ID
        （F03：删除后迟到写入不得重建会话）。新会话由服务端显式创建。

        S05 原子屏障：会话校验（含 tombstone 复查）与消息/标题写入在
        同一事务内完成，并对父会话行加锁（MySQL SELECT ... FOR UPDATE），
        与 clear_session 持同一把锁——删除先提交则本写入被拒，
        本写入先提交则删除等待锁释放后统一清理。
        """
        from fastapi import HTTPException, status

        async with AsyncSessionLocal() as db:
            existing_session = await db.run_sync(
                lambda s: s.query(ChatSession).filter(
                    ChatSession.id == session_id
                ).with_for_update().first()
            )

            if not existing_session or existing_session.deleted_at is not None:
                # 会话不存在或已删除：拒绝追加，不得重建
                logger.warning(
                    f"【数据库会话管理】会话 {session_id} 不存在或已删除，拒绝追加消息（F03 防复活）"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="会话不存在或已删除",
                )

            if existing_session.user_id != user_id:
                logger.warning(f"【数据库会话管理】会话 {session_id} 不属于用户 {user_id}，无法添加消息")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="当前会话不属于你，无法添加消息",
                )
            session = existing_session

            # 标题仍是默认值时，用首个问题做摘要（仅针对已存在会话，不自动创建）
            if session.title == "新的对话":
                title_summary = user_message[:30].strip()
                if len(user_message) > 30:
                    title_summary += "..."
                # SQLAlchemy 经典声明式 Column 赋值，mypy 误报为 Column[str]
                session.title = title_summary  # type: ignore[assignment]

            # 添加用户消息
            user_msg = ChatMessage(
                session_id=session.id,
                role="user",
                content=user_message
            )
            db.add(user_msg)

            # 添加助手消息
            assistant_msg = ChatMessage(
                session_id=session.id,
                role="assistant",
                content=assistant_message
            )
            db.add(assistant_msg)

            await db.commit()
            logger.info(f"【数据库会话管理】添加消息到会话: {session_id} 属于用户: {user_id}")

    async def get_history(self, session_id: str, user_id: str) -> List[Tuple[str, str]]:
        """获取会话历史"""
        session_data = await self.get_session(session_id, user_id)
        return session_data.get("history", [])

    async def clear_session(self, session_id: str, user_id: str):
        """清除会话及其关联数据（Agent 运行/步骤/反馈 + 聊天消息）。

        AgentRun/AgentStep/AgentFeedback 与会话只是普通字段关联（无外键级联），
        必须在此显式删除，否则会留下孤儿数据。全部在同一事务内完成：
        先删关联 Agent 数据，再删消息与会话；会话必须属于当前用户。

        S05 原子屏障：先取父会话行锁（与 add_message / finalize_agent_run
        同一把锁），锁内复查归属后统一清理并提交。对已是 tombstone 的会话
        重复删除同样执行清理（幂等），使历史竞态残留可被再次清除；
        tombstone 不保存标题、消息正文或 metadata。
        """
        async with AsyncSessionLocal() as db:
            # 查找会话并锁定行（校验归属 + 阻塞并发写入，直至本事务提交）
            session = await db.run_sync(
                lambda s: s.query(ChatSession).filter(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                ).with_for_update().first()
            )

            if session is None:
                return  # 不存在或不属于该用户：幂等返回

            # 该会话下的运行 ID（AgentRun.session_id 为普通字段）
            run_ids = select(AgentRun.id).where(AgentRun.session_id == session_id)
            await db.execute(delete(AgentFeedback).where(AgentFeedback.run_id.in_(run_ids)))
            await db.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
            await db.execute(delete(AgentRun).where(AgentRun.session_id == session_id))
            # 删除消息
            await db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
            # tombstone：保留最小行（清空标题/元数据），置删除时间，
            # 防止迟到写入把会话重建出来（F03）；重复删除时重置同样字段
            # （SQLAlchemy 经典声明式 Column 赋值，mypy 误报为 Column[...]）
            session.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)  # type: ignore[assignment]
            session.title = None  # type: ignore[assignment]
            session.metadata_ = None  # type: ignore[assignment]
            await db.commit()
            logger.info(
                f"【数据库会话管理】会话 {session_id} 及其关联数据已清除（tombstone），属于用户: {user_id}"
            )

    async def get_all_session_ids(self, user_id: Optional[str] = None) -> List[str]:
        """获取所有会话 ID，如果提供了 user_id，则只返回该用户的会话（不含已删除）"""
        async with AsyncSessionLocal() as db:
            if user_id:
                sessions = await db.run_sync(
                    lambda session: session.query(ChatSession).filter(
                        ChatSession.user_id == user_id,
                        ChatSession.deleted_at.is_(None),
                    ).all()
                )
            else:
                sessions = await db.run_sync(
                    lambda session: session.query(ChatSession).filter(
                        ChatSession.deleted_at.is_(None),
                    ).all()
                )
            # run_sync 的 Query 结果未经 ORM 注解，mypy 把字段推断为 Column[str]
            return [session.id for session in sessions]  # type: ignore[misc]

    async def get_user_sessions(self, user_id: str) -> List[Dict]:
        """获取用户所有会话详细信息（不含已删除）"""
        async with AsyncSessionLocal() as db:
            sessions = await db.run_sync(
                lambda session: session.query(ChatSession).filter(
                    ChatSession.user_id == user_id,
                    ChatSession.deleted_at.is_(None),
                ).all()
            )
            return [
                {
                    "id": session.id,
                    "title": session.title,
                    "created_at": session.created_at.isoformat() if session.created_at else None,
                    "updated_at": session.updated_at.isoformat() if session.updated_at else None
                }
                for session in sessions
            ]


# 全局数据库会话管理器实例
database_session_manager = None

# 初始化数据库会话管理器
async def init_database_session_manager():
    """
    初始化数据库会话管理器
    :return: 初始化完成的 DatabaseSessionManager 实例
    """
    global database_session_manager
    database_session_manager = await DatabaseSessionManager.create()
    return database_session_manager


def get_session_manager() -> "DatabaseSessionManager":
    """获取当前会话管理器实例（S01）。

    每次读取定义模块的当前全局变量，不做导入期快照；未初始化时明确失败，
    绝不返回 None —— 否则消费方会在 provider 调用之后才以
    AttributeError 的形式吞掉历史写入。
    """
    if database_session_manager is None:
        raise RuntimeError(
            "会话管理器尚未初始化（init_database_session_manager 未执行），"
            "拒绝进行会话读写"
        )
    return database_session_manager