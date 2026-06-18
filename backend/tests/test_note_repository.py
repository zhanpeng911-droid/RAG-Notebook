"""
NoteRepository 用户隔离测试 —— 验证数据访问层的 user_id 过滤。

测试策略：
- 使用 SQLite 内存数据库，验证真实 SQL 查询
- 覆盖风险点：
  - get_by_id: user_id 过滤
  - delete_by_id: user_id 过滤
  - get_by_ids: user_id 过滤
  - search_like: user_id 过滤
  - get_category_counts: user_id 过滤
"""
import uuid
import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.models.note import Note
from app.models.chat_history import Base
from app.repositories.note_repository import NoteRepository


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
USER_A = "user-a-0000-0000-000000000001"
USER_B = "user-b-0000-0000-000000000002"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


async def _seed(db: AsyncSession, user_id: str, count: int = 2):
    notes = []
    for i in range(count):
        note = Note(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=f"Note {i+1} by {user_id}",
            content=f"Content {i+1}",
            category="study",
            tags=["test"],
        )
        db.add(note)
        notes.append(note)
    await db.commit()
    return notes


# ==================== get_by_id ====================

@pytest.mark.asyncio
async def test_get_by_id_returns_own_note(db_session):
    notes = await _seed(db_session, USER_A)
    repo = NoteRepository()
    result = await repo.get_by_id(db_session, notes[0].id, USER_A)
    assert result is not None
    assert result.title == "Note 1 by user-a-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_get_by_id_rejects_other_user(db_session):
    notes = await _seed(db_session, USER_B)
    repo = NoteRepository()
    result = await repo.get_by_id(db_session, notes[0].id, USER_A)
    assert result is None


# ==================== delete_by_id ====================

@pytest.mark.asyncio
async def test_delete_by_id_returns_true_for_own_note(db_session):
    notes = await _seed(db_session, USER_A)
    repo = NoteRepository()
    result = await repo.delete_by_id(db_session, notes[0].id, USER_A)
    assert result is True
    # 验证已删除
    verify = await repo.get_by_id(db_session, notes[0].id, USER_A)
    assert verify is None


@pytest.mark.asyncio
async def test_delete_by_id_returns_false_for_other_user(db_session):
    notes = await _seed(db_session, USER_B)
    repo = NoteRepository()
    result = await repo.delete_by_id(db_session, notes[0].id, USER_A)
    assert result is False
    # 验证未被删除
    verify = await repo.get_by_id(db_session, notes[0].id, USER_B)
    assert verify is not None


# ==================== get_by_ids ====================

@pytest.mark.asyncio
async def test_get_by_ids_only_returns_own_notes(db_session):
    a_notes = await _seed(db_session, USER_A, count=2)
    b_notes = await _seed(db_session, USER_B, count=2)
    all_ids = [n.id for n in a_notes + b_notes]

    repo = NoteRepository()
    results = await repo.get_by_ids(db_session, all_ids, USER_A)
    assert len(results) == 2
    assert all(n.user_id == USER_A for n in results)


@pytest.mark.asyncio
async def test_get_by_ids_empty_list(db_session):
    repo = NoteRepository()
    results = await repo.get_by_ids(db_session, [], USER_A)
    assert results == []


# ==================== search_like ====================

@pytest.mark.asyncio
async def test_search_like_only_returns_own_notes(db_session):
    await _seed(db_session, USER_A, count=1)
    await _seed(db_session, USER_B, count=1)
    # 确保都有 "Note" 关键词
    repo = NoteRepository()
    results = await repo.search_like(db_session, USER_A, "Note")
    assert all(n.user_id == USER_A for n in results)


# ==================== get_category_counts ====================

@pytest.mark.asyncio
async def test_get_category_counts_only_counts_own_notes(db_session):
    await _seed(db_session, USER_A, count=3)
    await _seed(db_session, USER_B, count=5)

    repo = NoteRepository()
    counts = await repo.get_category_counts(db_session, USER_A)
    total = sum(counts.values())
    assert total == 3, f"用户 A 应该只有 3 条笔记，实际 {total}"
