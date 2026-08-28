"""DocumentIndexRepository / AgentRunRepository 数据访问层测试。

真实 SQLite 内存库驱动 ORM，重点验证 user_id 过滤确实进入 SQL、
状态机字段的条件写入与时间戳副作用。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chat_history import Base
from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.models.agent_run import AgentFeedback, AgentRunStatus
from app.repositories.document_index_repository import DocumentIndexRepository
from app.repositories.agent_run_repository import AgentRunRepository

USER_A = "user-a"
USER_B = "user-b"


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _doc(user_id, md5, original_filename="f.pdf", status=DocumentIndexStatus.UPLOADED,
         space_id=None, retry_count=None, created_at=None):
    return DocumentIndex(
        id=str(uuid.uuid4()), user_id=user_id, space_id=space_id,
        filename=f"{uuid.uuid4().hex}.pdf",
        original_filename=f"{md5}-{original_filename}",
        file_path="/tmp/f.pdf", md5=md5, status=status,
        retry_count=retry_count,
        created_at=created_at or datetime.now(timezone.utc).replace(tzinfo=None),
    )


# ---------- DocumentIndexRepository ----------

@pytest.mark.asyncio
async def test_create_defaults_and_persist(db_session):
    repo = DocumentIndexRepository(db_session)
    doc = await repo.create(USER_A, "x.pdf", "原始 X.pdf", "/tmp/x.pdf",
                            md5="M1", file_size=10, file_type="pdf",
                            space_id="s1")
    assert doc.status == DocumentIndexStatus.UPLOADED
    got = await repo.get_by_id(doc.id, USER_A)
    assert got.original_filename == "原始 X.pdf"
    assert got.space_id == "s1"


@pytest.mark.asyncio
async def test_get_by_id_rejects_other_user(db_session):
    repo = DocumentIndexRepository(db_session)
    doc = await repo.create(USER_A, "a.pdf", "A.pdf", "/p", "MA")
    assert await repo.get_by_id(doc.id, USER_B) is None


@pytest.mark.asyncio
async def test_get_by_md5_is_per_user(db_session):
    repo = DocumentIndexRepository(db_session)
    await repo.create(USER_A, "a.pdf", "a.pdf", "/p", "SHARED")
    assert await repo.get_by_md5("SHARED", USER_A) is not None
    assert await repo.get_by_md5("SHARED", USER_B) is None


@pytest.mark.asyncio
async def test_original_filename_unique_scoped_to_user(db_session):
    repo = DocumentIndexRepository(db_session)
    await repo.create(USER_A, "a.pdf", "同名.pdf", "/pa", "M-A")
    # B 用户上传同名不冲突，也查不到 A 的记录
    assert await repo.get_by_original_filename("同名.pdf", USER_A) is not None
    assert await repo.get_by_original_filename("同名.pdf", USER_B) is None
    await repo.create(USER_B, "b.pdf", "同名.pdf", "/pb", "M-B")
    both = {await repo.get_by_original_filename("同名.pdf", u)
            for u in (USER_A, USER_B)}
    assert all(both)


@pytest.mark.asyncio
async def test_get_user_documents_filters_space_and_status(db_session):
    session = db_session
    session.add_all([
        _doc(USER_A, "m1", space_id="s1", status=DocumentIndexStatus.INDEXED,
             created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)),
        _doc(USER_A, "m2", space_id="s2",
             created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)),
        _doc(USER_A, "m3", space_id="s1", status=DocumentIndexStatus.INDEX_FAILED,
             created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)),
        _doc(USER_B, "mb", space_id="s1"),
    ])
    await session.commit()
    repo = DocumentIndexRepository(session)

    out = await repo.get_user_documents(USER_A, space_id="s1")
    assert [d.md5 for d in out] == ["m3", "m1"]          # 时间倒序 + 空间过滤
    out2 = await repo.get_user_documents(USER_A, space_id="s1",
                                         status=DocumentIndexStatus.INDEXED)
    assert [d.md5 for d in out2] == ["m1"]
    all_a = await repo.get_user_documents(USER_A)
    assert len(all_a) == 3


@pytest.mark.asyncio
async def test_update_status_side_fields(db_session):
    repo = DocumentIndexRepository(db_session)
    doc = await repo.create(USER_A, "u.pdf", "u.pdf", "/p", "MU")
    await repo.update_status(doc.id, DocumentIndexStatus.INDEX_FAILED,
                             error_message="解析超时", chunk_count=0)
    failed = await repo.get_by_id(doc.id, USER_A)
    assert failed.status == DocumentIndexStatus.INDEX_FAILED
    assert failed.error_message == "解析超时"

    await repo.update_status(doc.id, DocumentIndexStatus.INDEXED, chunk_count=12)
    indexed = await repo.get_by_id(doc.id, USER_A)
    assert indexed.status == DocumentIndexStatus.INDEXED
    assert indexed.indexed_at is not None
    assert indexed.chunk_count == 12
    # 未传 error_message 时不清空旧值
    assert indexed.error_message == "解析超时"


@pytest.mark.asyncio
async def test_increment_retry_and_delete(db_session):
    repo = DocumentIndexRepository(db_session)
    row = _doc(USER_A, "mr", retry_count=None)
    db_session.add(row)
    await db_session.commit()

    await repo.increment_retry(row.id)
    await repo.increment_retry(row.id)
    fresh = await repo.get_by_id(row.id, USER_A)
    assert fresh.retry_count == 2

    assert await repo.delete_by_id(row.id, USER_B) is False   # 跨用户删除拒绝
    assert await repo.delete_by_id(row.id, USER_A) is True
    assert await repo.get_by_id(row.id, USER_A) is None


@pytest.mark.asyncio
async def test_pending_and_failed_queries(db_session):
    s = db_session
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    s.add_all([
        _doc(USER_A, "p1", status=DocumentIndexStatus.PENDING_INDEX,
             created_at=old),
        _doc(USER_A, "p2", status=DocumentIndexStatus.PENDING_INDEX),
        _doc(USER_A, "f1", status=DocumentIndexStatus.INDEX_FAILED),
        _doc(USER_A, "done", status=DocumentIndexStatus.INDEXED),
        _doc(USER_B, "fb", status=DocumentIndexStatus.INDEX_FAILED),
    ])
    await s.commit()
    repo = DocumentIndexRepository(s)

    pending = await repo.get_pending_documents(limit=10)
    assert [d.md5 for d in pending] == ["p1", "p2"]      # 升序、仅待索引
    assert len(await repo.get_pending_documents(limit=1)) == 1

    failed_a = await repo.get_failed_documents(USER_A)
    assert [d.md5 for d in failed_a] == ["f1"]
    assert [d.md5 for d in await repo.get_failed_documents(USER_B)] == ["fb"]


# ---------- AgentRunRepository ----------

@pytest.mark.asyncio
async def test_agent_run_lifecycle(db_session):
    repo = AgentRunRepository(db_session)
    run = await repo.create_run(USER_A, "什么是索引", session_id="sess-1",
                                model_config={"llm": "ollama"})
    assert run.status == AgentRunStatus.STARTED
    assert run.completed_at is None

    await repo.update_run(run.id, query_type="FACTUAL",
                          retrieval_rounds=2, evidence_count=5,
                          citation_count=3, total_time_ms=1234)
    mid = await repo.get_run(run.id, USER_A)
    assert mid.completed_at is None                      # 非终结态不打时间戳

    await repo.update_run(run.id, status=AgentRunStatus.COMPLETED,
                          answer="答案")
    done = await repo.get_run(run.id, USER_A)
    assert done.answer == "答案" and done.completed_at is not None
    assert done.model_config == {"llm": "ollama"}

    assert await repo.get_run(run.id, USER_B) is None    # 隔离


@pytest.mark.asyncio
async def test_get_user_runs_filters_session_and_limit(db_session):
    repo = AgentRunRepository(db_session)
    await repo.create_run(USER_A, "q1", session_id="s1")
    await repo.create_run(USER_A, "q2", session_id="s2")
    await repo.create_run(USER_B, "qb", session_id="s1")

    mine_s1 = await repo.get_user_runs(USER_A, session_id="s1")
    assert [r.query for r in mine_s1] == ["q1"]
    limited = await repo.get_user_runs(USER_A, limit=1)
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_steps_and_feedback(db_session):
    repo = AgentRunRepository(db_session)
    run = await repo.create_run(USER_A, "q")

    for i, phase in enumerate(["plan", "retrieve"]):
        await repo.add_step(run.id, USER_A, phase,
                            step_data={"round": i}, duration_ms=100 * (i + 1))

    steps = await repo.get_run_steps(run.id)
    assert [st.phase for st in steps] == ["plan", "retrieve"]
    assert [st.step_data["round"] for st in steps] == [0, 1]

    feedback = await repo.add_feedback(run.id, USER_A, rating=5,
                                       comment="不错")
    assert isinstance(feedback, AgentFeedback) and feedback.rating == 5
