"""index_task 测试 —— 后台索引核心逻辑、失败状态机与批量补偿。

异步核心直接调用（不经过 Celery broker）；数据库走真实 SQLite 内存库；
VectorStoreService 与 index_document_task.delay 经 sys.modules mock 模块
注入，全程不引入全局 langchain 污染。
"""
import uuid
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.tasks.index_task as it  # noqa: E402


class RetrySignal(Exception):
    """模拟 Celery Task.retry 抛出的重试信号。"""
from app.models.chat_history import Base  # noqa: E402
from app.models.document_index import DocumentIndex, DocumentIndexStatus  # noqa: E402
from app.repositories.document_index_repository import DocumentIndexRepository  # noqa: E402


def _fake_task():
    t = NS(request=NS(retries=0, max_retries=3))

    def retry(exc=None):
        raise AssertionError("不应触发重试")

    t.retry = retry
    return t


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    import app.db.db_config as db_cfg
    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", factory)

    # 向量库桩：挂在 conftest 预注册的 mock 模块上
    import sys
    store_impl = NS(
        get_file_document=None,
        split_documents_sync=lambda docs: docs,
        vectors_store=NS(add_documents=lambda docs: None),
        save_md5_hex_seen=[],
    )

    async def _save_md5(m, filename=None, original=None, u=None):
        store_impl.save_md5_hex_seen.append((m, filename))

    store_impl.save_md5_hex = _save_md5

    class FakeVSCls:
        def __new__(cls):
            return store_impl

    monkeypatch.setattr(sys.modules["app.rag.vector_store"],
                        "VectorStoreService", FakeVSCls)

    # Celery delay 桩
    box = {"delays": []}
    import app.tasks.index_task as mod

    def _delay(doc_id, user_id):
        box["delays"].append((doc_id, user_id))
        return NS(id="t")

    monkeypatch.setattr(mod, "index_document_task",
                        NS(delay=staticmethod(_delay)))
    monkeypatch.setattr(mod, "batch_index_pending_task",
                        NS(delay=staticmethod(_delay)))

    yield NS(factory=factory, store=store_impl, delays=box["delays"],
             tmp=tmp_path)
    await engine.dispose()


async def _seed_doc(factory, user_id="u1", status=DocumentIndexStatus.PENDING_INDEX,
                    retry_count=0, file_exists=True, tmp=None):
    path = None
    if file_exists and tmp is not None:
        path = str(tmp / f"{uuid.uuid4().hex}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("正文内容")
    async with factory() as s:
        repo = DocumentIndexRepository(s)
        md5 = f"M{uuid.uuid4().hex[:6]}"
        doc = await repo.create(user_id, f"{md5}.pdf", f"{md5}-x.pdf",
                                path or "/missing/file.pdf",
                                md5=md5, status=status)
        if retry_count:
            doc.retry_count = retry_count
        await s.commit()
        return doc.id, doc.md5, path


# ---------- 清理函数 ----------

def test_sanitize_error_message():
    msg = 'call failed with sk-abcdef1234567890 Bearer aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa password=secret123'
    out = it._sanitize_error_message(msg)
    assert "sk-***" in out and "sk-abcdef" not in out
    assert "Bearer ***" in out
    assert "password=***" in out
    assert "secret123" not in out


# ---------- _index_document_async ----------

@pytest.mark.asyncio
async def test_index_missing_record_returns_quietly(env):
    await it._index_document_async(_fake_task(), str(uuid.uuid4()), "nobody")


@pytest.mark.asyncio
async def test_index_missing_file_marks_failed(env):
    doc_id, _, _ = await _seed_doc(env.factory, file_exists=False)
    await it._index_document_async(_fake_task(), doc_id, "u1")
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        doc = await repo.get_by_id(doc_id, "u1")
        assert doc.status == DocumentIndexStatus.INDEX_FAILED
        assert "文件已丢失" in doc.error_message


@pytest.mark.asyncio
async def test_index_success_sets_indexed(env):
    doc_id, md5, _ = await _seed_doc(env.factory, tmp=env.tmp)

    async def loader(path, md5=None, user_id=None):
        return [NS(page_content="甲", metadata={"source": path}),
                NS(page_content="乙", metadata={"source": path})]

    env.store.get_file_document = loader
    await it._index_document_async(_fake_task(), doc_id, "u1")

    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        doc = await repo.get_by_id(doc_id, "u1")
        assert doc.status == DocumentIndexStatus.INDEXED
        assert doc.chunk_count == 2
        assert env.store.save_md5_hex_seen[0][0] == md5


@pytest.mark.asyncio
async def test_index_empty_load_marks_failed(env):
    doc_id, _, _ = await _seed_doc(env.factory, tmp=env.tmp)

    async def empty_loader(*a, **k):
        return []

    env.store.get_file_document = empty_loader
    await it._index_document_async(_fake_task(), doc_id, "u1")
    async with env.factory() as s:
        doc = await s.get(DocumentIndex, doc_id)
        assert doc.status == DocumentIndexStatus.INDEX_FAILED
        assert "加载为空" in doc.error_message


@pytest.mark.asyncio
async def test_index_loader_exception_failed_and_retry(env):
    doc_id, _, _ = await _seed_doc(env.factory, tmp=env.tmp)

    async def boom(*a, **k):
        raise RuntimeError("sk-aaaaaaaaaaaaaaaa embedding down")

    env.store.get_file_document = boom

    class TaskCtl:
        max_retries = 3

        def __init__(self):
            self.request = NS(retries=0, max_retries=3)
            self.retried = None

        def retry(self, exc=None):
            # 模拟 Celery：记录原始异常并抛出重试信号
            self.retried = exc
            raise RetrySignal()

    t = TaskCtl()
    with pytest.raises(RetrySignal):
        await it._index_document_async(t, doc_id, "u1")
    assert isinstance(t.retried, RuntimeError)   # 还有次数 → 触发 retry
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        doc = await repo.get_by_id(doc_id, "u1")
        assert doc.status == DocumentIndexStatus.INDEX_FAILED
        assert "sk-***" in doc.error_message      # 敏感信息已脱敏
        assert "embedding down" in doc.error_message
        assert doc.retry_count == 1


@pytest.mark.asyncio
async def test_index_exception_without_retries_left_stops(env):
    doc_id, _, _ = await _seed_doc(env.factory, tmp=env.tmp)

    async def boom(*a, **k):
        raise RuntimeError("磁盘错误")

    env.store.get_file_document = boom

    class Exhausted:
        max_retries = 3

        def __init__(self):
            self.request = NS(retries=3, max_retries=3)

        def retry(self, exc=None):
            raise AssertionError("不应再重试")

    await it._index_document_async(Exhausted(), doc_id, "u1")
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        doc = await repo.get_by_id(doc_id, "u1")
        assert doc.status == DocumentIndexStatus.INDEX_FAILED


# ---------- 批量补偿 ----------

@pytest.mark.asyncio
async def test_batch_submits_pending_and_failed_below_retry_cap(env):
    p_id, _, _ = await _seed_doc(env.factory, tmp=env.tmp,
                                 status=DocumentIndexStatus.PENDING_INDEX)
    f_id, _, _ = await _seed_doc(env.factory, tmp=env.tmp,
                                 status=DocumentIndexStatus.INDEX_FAILED,
                                 retry_count=2)
    done_id, _, _ = await _seed_doc(env.factory, tmp=env.tmp,
                                    status=DocumentIndexStatus.INDEXED)
    cap_id, _, _ = await _seed_doc(env.factory, tmp=env.tmp,
                                   status=DocumentIndexStatus.INDEX_FAILED,
                                   retry_count=3)

    await it._batch_index_pending_async(limit=10)
    submitted = {d[0] for d in env.delays}
    assert submitted == {p_id, f_id}
    assert done_id not in submitted and cap_id not in submitted

    # 失败文档被重置为 pending_index
    async with env.factory() as s:
        repo = DocumentIndexRepository(s)
        fdoc = await repo.get_by_id(f_id, "u1")
        assert fdoc.status == DocumentIndexStatus.PENDING_INDEX


@pytest.mark.asyncio
async def test_batch_no_work_short_circuits(env):
    await it._batch_index_pending_async(limit=5)
    assert env.delays == []
