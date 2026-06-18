"""
Chat 路由 —— 处理 AI 对话和 RAG 检索的 API 接口。

接口列表：
- POST /chat/agent/query/stream  —— Agent 流式对话（SSE）
- POST /chat/rag/query           —— RAG 检索（非流式）
- GET  /chat/session/{session_id} —— 获取会话历史
- DELETE /chat/session/{session_id} —— 删除会话
- GET  /chat/sessions            —— 获取所有会话
"""
import uuid

from fastapi.routing import APIRouter
from fastapi import Depends
from fastapi.responses import StreamingResponse

from app.agent.agent import get_agent_stream_response
from app.router.chat_service import ChatService, get_router_service

from app.schemas.models import QueryRequest, RAGResponse, RAGRequest, SessionResponse
from app.utils.auth_utils import get_current_user_id
from app.core.success_response import success_response
from app.core.rate_limit import rate_limit

chat_router = APIRouter(prefix="/chat", tags=["chat"])


@chat_router.post("/agent/query/stream")
async def query_stream(
        request: QueryRequest,
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """查询Agent流式响应"""
    session_id = request.session_id or str(uuid.uuid4())
    llm_config = request.llm_config.model_dump() if request.llm_config else None

    return StreamingResponse(
        get_agent_stream_response(
            request.query, session_id, user_id, llm_config=llm_config
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


@chat_router.post("/rag/query")
async def query_rag(
        request: RAGRequest,
        user_id: str = Depends(get_current_user_id),
        router_service: ChatService = Depends(get_router_service),
        _: None = Depends(rate_limit(limit=15, window=60))
):
    """RAG检索"""
    llm_config = request.llm_config.model_dump() if request.llm_config else None
    response = await router_service.handle_rag_query(request.query, user_id, llm_config=llm_config)
    return success_response(data=RAGResponse(response=response))


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
