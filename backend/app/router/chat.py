"""
Chat 路由 -- 处理会话管理的 API 接口。

接口列表：
- GET  /chat/session/{session_id} -- 获取会话历史
- DELETE /chat/session/{session_id} -- 删除会话
- GET  /chat/sessions            -- 获取所有会话

注意：Agent 对话和 RAG 检索已迁移至 agent_router（/chat/agent/query）。
"""

from fastapi.routing import APIRouter
from fastapi import Depends

from app.router.chat_service import ChatService, get_router_service

from app.schemas.models import SessionResponse
from app.utils.auth_utils import get_current_user_id
from app.core.success_response import success_response

chat_router = APIRouter(prefix="/chat", tags=["chat"])


@chat_router.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, user_id: str = Depends(get_current_user_id),
                      router_service: ChatService = Depends(get_router_service)):
    """获取会话信息，使用user_id验证"""
    history = await router_service.handle_get_session(session_id, user_id)
    return success_response(data=SessionResponse(session_id=session_id, history=history))


@chat_router.delete("/session/{session_id}")
async def delete_session(session_id: str, user_id: str = Depends(get_current_user_id),
                         router_service: ChatService = Depends(get_router_service)):
    """删除会话"""
    await router_service.handle_delete_session(session_id, user_id)
    return success_response(message=f"Session {session_id} deleted successfully")


@chat_router.get("/sessions")
async def get_all_sessions(
        user_id: str = Depends(get_current_user_id),
        router_service: ChatService = Depends(get_router_service)
):
    """获取当前用户的所有会话"""
    session_ids = await router_service.handle_get_user_sessions(user_id, user_id)
    return success_response(data={"sessions": session_ids})


@chat_router.get("/sessions/{user_id}")
async def get_user_sessions(user_id: str, current_user_id: str = Depends(get_current_user_id),
                            router_service: ChatService = Depends(get_router_service)):
    """获取用户所有会话ID"""
    session_ids = await router_service.handle_get_user_sessions(user_id, current_user_id)
    return success_response(data={"sessions": session_ids})
