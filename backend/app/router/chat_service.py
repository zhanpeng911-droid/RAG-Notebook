from typing import List, Tuple, Dict

from fastapi import HTTPException

from app.services import session_manager as sm


class ChatService:
    """路由服务层，处理会话管理业务逻辑。

    注意：RAG 查询和 Agent 对话已迁移至 agent_router，
    本服务仅保留会话历史管理功能。
    """

    async def handle_get_session(self, session_id: str, user_id: str) -> List[Tuple[str, str]]:
        """处理获取会话逻辑"""
        history = await sm.session_manager.get_history(session_id, user_id)
        return history

    async def handle_delete_session(self, session_id: str, user_id: str) -> None:
        """处理删除会话逻辑"""
        await sm.session_manager.clear_session(session_id, user_id)

    async def handle_get_all_sessions(self) -> List[str]:
        """处理获取所有会话逻辑"""
        session_ids = await sm.session_manager.get_all_session_ids()
        return session_ids

    async def handle_get_user_sessions(self, user_id: str, current_user_id: str) -> List[Dict]:
        """处理获取用户会话逻辑"""
        if user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        sessions = await sm.session_manager.get_user_sessions(user_id)
        return sessions


def get_router_service() -> ChatService:
    """获取路由服务实例（用于依赖注入）"""
    return ChatService()
