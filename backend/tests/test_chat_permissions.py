"""
Chat 会话权限测试 —— 验证用户 A 不能访问用户 B 的会话。

测试策略：
- 直接调用 ChatService 方法，mock 底层 session_manager
- 覆盖风险点：
  - handle_get_user_sessions: 跨用户访问（应返回 403）
  - handle_get_user_sessions: 同用户访问（应正常返回）
  - handle_get_session: 跨用户访问（应返回 403）
  - handle_delete_session: 跨用户删除（应静默忽略或报错）
"""
import pytest
from unittest.mock import patch, AsyncMock
from fastapi import HTTPException


USER_A = "user-a-0000-0000-000000000001"
USER_B = "user-b-0000-0000-000000000002"


def _get_chat_service():
    """延迟导入 ChatService，确保 conftest.py 的 mock 已生效"""
    from app.router.chat_service import ChatService
    return ChatService()


# ==================== 测试用例 ====================


@pytest.mark.asyncio
async def test_get_user_sessions_forbidden_when_cross_user():
    """用户 A 请求用户 B 的会话列表时，应返回 403"""
    service = _get_chat_service()

    with pytest.raises(HTTPException) as exc_info:
        await service.handle_get_user_sessions(user_id=USER_B, current_user_id=USER_A)

    assert exc_info.value.status_code == 403
    assert "Forbidden" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_user_sessions_allowed_for_same_user():
    """用户 A 请求自己的会话列表时，应正常返回"""
    service = _get_chat_service()

    mock_sessions = [{"id": "s1", "title": "Session 1"}]
    with patch("app.router.chat_service.sm") as mock_sm:
        mock_sm.session_manager.get_user_sessions = AsyncMock(return_value=mock_sessions)
        result = await service.handle_get_user_sessions(user_id=USER_A, current_user_id=USER_A)

    assert result == mock_sessions


@pytest.mark.asyncio
async def test_get_session_cross_user_returns_403():
    """
    用户 A 尝试获取用户 B 的会话历史。

    get_history 调用 get_session，get_session 中：
    - 如果 session_id 存在但不属于 user_id → 抛 403
    """
    service = _get_chat_service()

    with patch("app.router.chat_service.sm") as mock_sm:
        mock_sm.session_manager.get_history = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="当前会话不属于你")
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.handle_get_session("session-of-user-b", USER_A)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_session_cross_user_no_effect():
    """
    用户 A 尝试删除用户 B 的会话。

    clear_session 使用 user_id 过滤查询，跨用户删除不会命中任何记录，
    因此静默忽略（不报错也不删除）。
    """
    service = _get_chat_service()

    with patch("app.router.chat_service.sm") as mock_sm:
        mock_sm.session_manager.clear_session = AsyncMock(return_value=None)
        await service.handle_delete_session("session-of-user-b", USER_A)
        mock_sm.session_manager.clear_session.assert_called_once_with("session-of-user-b", USER_A)


@pytest.mark.asyncio
async def test_handle_get_all_sessions_no_user_filter():
    """
    get_all_sessions 不带 user_id 过滤，返回所有会话。

    这是一个潜在风险点：如果前端误用此接口，可能泄露所有用户的会话 ID。
    当前实现中此接口仅用于管理员/调试目的。
    """
    service = _get_chat_service()

    mock_ids = ["session-1", "session-2", "session-3"]
    with patch("app.router.chat_service.sm") as mock_sm:
        mock_sm.session_manager.get_all_session_ids = AsyncMock(return_value=mock_ids)
        result = await service.handle_get_all_sessions()

    assert result == mock_ids
