"""
知识库权限测试 —— 验证 _ensure_space_member 和空间访问控制。

测试策略：
- Mock 数据库会话，验证 SQL 查询逻辑
- 覆盖风险点：
  - _ensure_space_member: space_id 为空时返回 None
  - _ensure_space_member: 空间不存在时返回 404
  - _ensure_space_member: 用户非组织成员时返回 403
  - _ensure_space_member: 用户是组织成员时正常返回
  - add_note_to_space: 只允许加入自己的笔记
"""
import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException


USER_A = "user-a-0000-0000-000000000001"
USER_B = "user-b-0000-0000-000000000002"
ORG_ID = "org-0000-0000-0000-000000000099"
SPACE_ID = "space-0000-0000-0000-000000000088"


def _get_ensure_space_member():
    """延迟导入 _ensure_space_member"""
    from app.router.knowledge_router import _ensure_space_member
    return _ensure_space_member


def _make_mock_db(space=None, member=None):
    """创建 mock 数据库会话，按查询顺序返回结果"""
    mock_db = AsyncMock()

    results = []
    # 第一次 execute: 查询 Space
    space_result = MagicMock()
    space_result.scalar_one_or_none.return_value = space
    results.append(space_result)
    # 第二次 execute: 查询 OrganizationMember
    member_result = MagicMock()
    member_result.scalar_one_or_none.return_value = member
    results.append(member_result)

    mock_db.execute = AsyncMock(side_effect=results)
    return mock_db


# ==================== 测试用例 ====================


@pytest.mark.asyncio
async def test_ensure_space_member_none_space_id():
    """space_id 为空时，应返回 None（不校验权限）"""
    ensure_fn = _get_ensure_space_member()
    mock_db = _make_mock_db()
    result = await ensure_fn(None, USER_A, mock_db)
    assert result is None


@pytest.mark.asyncio
async def test_ensure_space_member_empty_space_id():
    """space_id 为空字符串时，应返回 None"""
    ensure_fn = _get_ensure_space_member()
    mock_db = _make_mock_db()
    result = await ensure_fn("", USER_A, mock_db)
    assert result is None


@pytest.mark.asyncio
async def test_ensure_space_member_space_not_found():
    """空间不存在时，应返回 404"""
    ensure_fn = _get_ensure_space_member()
    mock_db = _make_mock_db(space=None)

    with pytest.raises(HTTPException) as exc_info:
        await ensure_fn(SPACE_ID, USER_A, mock_db)

    assert exc_info.value.status_code == 404
    assert "空间不存在" in exc_info.value.detail


@pytest.mark.asyncio
async def test_ensure_space_member_not_org_member():
    """用户非该空间所属组织成员时，应返回 403"""
    space = MagicMock()
    space.id = SPACE_ID
    space.org_id = ORG_ID

    mock_db = _make_mock_db(space=space, member=None)

    ensure_fn = _get_ensure_space_member()
    with pytest.raises(HTTPException) as exc_info:
        await ensure_fn(SPACE_ID, USER_A, mock_db)

    assert exc_info.value.status_code == 403
    assert "组织的成员" in exc_info.value.detail


@pytest.mark.asyncio
async def test_ensure_space_member_is_org_member():
    """用户是组织成员时，应正常返回 Space 对象"""
    space = MagicMock()
    space.id = SPACE_ID
    space.org_id = ORG_ID

    member = MagicMock()
    member.role = "member"

    mock_db = _make_mock_db(space=space, member=member)

    ensure_fn = _get_ensure_space_member()
    result = await ensure_fn(SPACE_ID, USER_A, mock_db)
    assert result is not None
    assert result.id == SPACE_ID


@pytest.mark.asyncio
async def test_ensure_space_member_admin_role():
    """admin 角色也应通过权限验证"""
    space = MagicMock()
    space.id = SPACE_ID
    space.org_id = ORG_ID

    member = MagicMock()
    member.role = "admin"

    mock_db = _make_mock_db(space=space, member=member)

    ensure_fn = _get_ensure_space_member()
    result = await ensure_fn(SPACE_ID, USER_A, mock_db)
    assert result is not None


@pytest.mark.asyncio
async def test_add_note_to_space_rejects_other_users_note():
    """
    add_note_to_space 只允许加入自己的笔记。

    当用户 A 尝试把用户 B 的笔记加入空间时，应返回 404。
    """
    from app.router.space_router import add_note_to_space

    space = MagicMock()
    space.id = SPACE_ID
    space.org_id = ORG_ID

    member = MagicMock()
    member.role = "member"

    # 查询结果顺序：space → role → note（note 不存在因为 user_id 不匹配）
    space_result = MagicMock()
    space_result.scalar_one_or_none.return_value = space

    role_result = MagicMock()
    role_result.scalar_one_or_none.return_value = member

    note_result = MagicMock()
    note_result.scalar_one_or_none.return_value = None  # 用户 A 的笔记不存在

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[space_result, role_result, note_result])

    with pytest.raises(HTTPException) as exc_info:
        await add_note_to_space(
            space_id=SPACE_ID,
            note_id="note-of-user-b",
            user_id=USER_A,
            db=mock_db,
        )

    assert exc_info.value.status_code == 404
    assert "只能加入自己的笔记" in exc_info.value.detail
