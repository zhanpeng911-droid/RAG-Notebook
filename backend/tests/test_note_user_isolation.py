"""
笔记用户隔离测试 —— 验证用户 A 不能访问用户 B 的笔记。

测试策略：
- 使用 SQLite 内存数据库替代 MySQL，避免外部依赖
- Mock NoteVectorIndex，仅验证业务逻辑正确性
- 覆盖风险点：
  - get_note: 跨用户读取
  - update_note: 跨用户更新
  - delete_note: 跨用户删除
  - list_notes: 跨用户列表泄露
  - search_related_notes: 向量检索是否正确委托给 NoteVectorIndex
"""
import uuid
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.models.note import Note
from app.models.review_record import ReviewRecord  # noqa: F401 — 注册表结构
from app.models.chat_history import Base


# ==================== 测试数据库 Fixtures ====================

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    """创建内存 SQLite 引擎并建表"""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """每个测试用例独立的数据库会话"""
    session_factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ==================== 测试数据 ====================

USER_A = "user-a-0000-0000-000000000001"
USER_B = "user-b-0000-0000-000000000002"


async def _seed_notes(db: AsyncSession, user_id: str, count: int = 3):
    """向数据库插入指定用户的测试笔记"""
    notes = []
    for i in range(count):
        note = Note(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=f"Test Note {i+1}",
            content=f"Content for note {i+1} by {user_id}",
            category="study",
            tags=["test"],
        )
        db.add(note)
        notes.append(note)
    await db.commit()
    return notes


def _get_note_service():
    """延迟导入 NoteService，用 mock NoteVectorIndex 避免真实 ChromaDB"""
    with patch("app.services.note_service.NoteVectorIndex"):
        from app.services.note_service import NoteService
        return NoteService()


# ==================== 测试用例 ====================


@pytest.mark.asyncio
async def test_get_note_user_a_cannot_read_user_b_note(db_session):
    """用户 A 不能通过 get_note 读取用户 B 的笔记"""
    b_notes = await _seed_notes(db_session, USER_B, count=1)
    b_note_id = b_notes[0].id

    service = _get_note_service()
    result = await service.get_note(db_session, b_note_id, USER_A)
    assert result is None, "用户 A 不应该能读取用户 B 的笔记"


@pytest.mark.asyncio
async def test_get_note_user_can_read_own_note(db_session):
    """用户 A 可以正常读取自己的笔记"""
    a_notes = await _seed_notes(db_session, USER_A, count=1)
    a_note_id = a_notes[0].id

    service = _get_note_service()
    result = await service.get_note(db_session, a_note_id, USER_A)
    assert result is not None, "用户应该能读取自己的笔记"
    assert result.title == "Test Note 1"


@pytest.mark.asyncio
async def test_update_note_user_a_cannot_update_user_b_note(db_session):
    """用户 A 不能更新用户 B 的笔记（应返回 None）"""
    from app.schemas.models import NoteUpdate

    b_notes = await _seed_notes(db_session, USER_B, count=1)
    b_note_id = b_notes[0].id

    service = _get_note_service()
    payload = NoteUpdate(title="Hacked Title", content="Hacked Content")
    result = await service.update_note(db_session, b_note_id, USER_A, payload)
    assert result is None, "用户 A 不应该能更新用户 B 的笔记"

    # 验证原始笔记未被修改
    b_note = await service.get_note(db_session, b_note_id, USER_B)
    assert b_note.title == "Test Note 1", "用户 B 的笔记标题不应被修改"


@pytest.mark.asyncio
async def test_delete_note_user_a_cannot_delete_user_b_note(db_session):
    """用户 A 不能删除用户 B 的笔记（应返回 False）"""
    b_notes = await _seed_notes(db_session, USER_B, count=1)
    b_note_id = b_notes[0].id

    service = _get_note_service()
    result = await service.delete_note(db_session, b_note_id, USER_A)
    assert result is False, "用户 A 不应该能删除用户 B 的笔记"

    b_note = await service.get_note(db_session, b_note_id, USER_B)
    assert b_note is not None, "用户 B 的笔记应该仍然存在"


@pytest.mark.asyncio
async def test_list_notes_only_returns_own_notes(db_session):
    """list_notes 只返回当前用户的笔记，不包含其他用户笔记"""
    await _seed_notes(db_session, USER_A, count=2)
    await _seed_notes(db_session, USER_B, count=3)

    service = _get_note_service()
    notes, total = await service.list_notes(db_session, USER_A)
    assert total == 2, f"用户 A 应该只有 2 条笔记，实际 {total}"
    assert all(n.user_id == USER_A for n in notes), "列表中不应包含其他用户的笔记"


@pytest.mark.asyncio
async def test_search_related_notes_delegates_to_note_index(db_session):
    """search_related_notes 正确委托给 note_index.search_related_notes"""
    service = _get_note_service()
    mock_index = MagicMock()
    mock_index.search_related_notes.return_value = []
    service.note_index = mock_index

    await service.search_related_notes("test query", USER_A, top_k=5)

    mock_index.search_related_notes.assert_called_once_with("test query", USER_A, 5)


@pytest.mark.asyncio
async def test_search_notes_delegates_to_note_index(db_session):
    """search_notes 正确委托给 note_index.search_user_notes"""
    service = _get_note_service()
    mock_index = MagicMock()
    mock_index.search_user_notes.return_value = []
    service.note_index = mock_index

    await service.search_notes(db_session, USER_A, "test query", top_k=5)

    mock_index.search_user_notes.assert_called_once_with("test query", USER_A, 5)


@pytest.mark.asyncio
async def test_create_note_writes_review_without_inline_vector_index(db_session):
    """create_note：MySQL + 复习队列同事务；向量索引由路由异步投递，服务层不内联调用。"""
    from app.schemas.models import NoteCreate
    from sqlalchemy import select

    service = _get_note_service()
    mock_index = MagicMock()
    service.note_index = mock_index

    payload = NoteCreate(title="New Note", content="Content")
    note = await service.create_note(db_session, USER_A, payload)

    assert note is not None
    assert note.user_id == USER_A
    mock_index.add_note.assert_not_called()

    result = await db_session.execute(
        select(ReviewRecord).where(
            ReviewRecord.note_id == note.id,
            ReviewRecord.user_id == USER_A,
        )
    )
    assert result.scalar_one_or_none() is not None, "创建笔记后应写入复习队列"


@pytest.mark.asyncio
async def test_delete_note_clears_mysql_without_inline_vector_cleanup(db_session):
    """delete_note：仅清理 MySQL；向量清理由路由异步投递，服务层不内联调用。"""
    notes = await _seed_notes(db_session, USER_A, count=1)
    note_id = notes[0].id

    service = _get_note_service()
    mock_index = MagicMock()
    service.note_index = mock_index

    result = await service.delete_note(db_session, note_id, USER_A)
    assert result is True
    mock_index.delete_note.assert_not_called()
    assert await service.get_note(db_session, note_id, USER_A) is None
