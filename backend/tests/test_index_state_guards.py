"""S02/S04 索引状态守卫回归 —— deleting 不可被覆盖，条件发布结果必须传播。

S02：worker 异常重试、缺文件分支、人工重试、Beat 补偿都不得把
deleting 改回 pending_index / index_failed / pending_index。
S04：publish_indexed 返回 False 时 _sync_index 不得返回 indexed。
"""
import uuid
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.tasks.index_task as it
from app.models.chat_history import Base
from app.models.document_index import DocumentIndexStatus
from app.repositories.document_index_repository import DocumentIndexRepository


class RetrySignal(Exception):
    """模拟 Celery Task.retry 抛出的重试信号。"""


def _fake_task():
    t = NS(request=NS(retries=0, max_retries=3))

    def retry(exc=None):
        raise AssertionError("不应触发重试")

    t.retry = retry
    return t


class TaskCtl:
    max_retries = 3

    def __init__(self, retries=0):
        self.request = NS(retries=retries, max_retries=3)
        self.retried = None

    def retry(self, exc=None):
        self.retried = exc
        raise RetrySignal()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    import app.db.db_config as db_cfg
    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", factory)

    import sys
    store_impl = NS(
        get_file_document=None,
        split_documents_sync=lambda docs: docs,
        vectors_store=NS(
            add_documents=lambda docs, **kwargs: None,
            delete=lambda where=None, ids=None: None,
        ),
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

    def _delay(doc_id, user_id):
        box["delays"].append((doc_id, user_id))
        return NS(id="t")

    monkeypatch.setattr(it, "index_document_task",
                        NS(delay=staticmethod(_delay)))
    monkeypatch.setattr(it, "batch_index_pending_task",
                        NS(delay=staticmethod(_delay)))

    yield NS(factory=factory, store=store_impl, delays=box["delays"],
             tmp=tmp_path)
    await engine.dispose()


async def _seed_doc(factory, user_id="u1", status=DocumentIndexStatus.PENDING_INDEX,
                    file_exists=True, tmp=None):
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
        await s.commit()
        return doc.id, doc.md5


async def _status_of(factory, doc_id, user_id="u1"):
    async with factory() as s:
        doc = await DocumentIndexRepository(s).get_by_id(doc_id, user_id)
        return doc.status if doc else None


# ---------- S02-a：worker 异常路径不得覆盖 deleting ----------

@pytest.mark.asyncio
async def test_worker_exception_after_delete_wins_keeps_deleting(env):
    """worker 领取后删除获胜（deleting），loader 抛错不得改回 pending_index。

    修复前行为：状态被无条件改回 pending_index 并投递 retry（S02 回归）。
    修复后行为：失去执行权 → 保留 deleting，不投递重试。
    """
    doc_id, _ = await _seed_doc(env.factory, tmp=env.tmp)

    async def loader(path, md5=None, user_id=None):
        # 删除获胜：worker 持有 indexing 期间文档被置为 deleting
        async with env.factory() as s:
            await DocumentIndexRepository(s).update_status(
                doc_id, DocumentIndexStatus.DELETING)
            await s.commit()
        raise RuntimeError("embedding down")

    env.store.get_file_document = loader

    t = TaskCtl()
    try:
        await it._index_document_async(t, doc_id, "u1")
    except RetrySignal:
        pass  # 修复前会在此触发无条件重试
    assert t.retried is None, "失去执行权（deleting）时不得投递重试"
    assert await _status_of(env.factory, doc_id) == DocumentIndexStatus.DELETING


# ---------- S02-b：缺文件分支不得在 claim 前写终态 ----------

@pytest.mark.asyncio
async def test_worker_missing_file_on_deleting_doc_writes_nothing(env):
    """deleting 文档的迟到/重复任务（缺文件）不得写成 index_failed。"""
    doc_id, _ = await _seed_doc(env.factory,
                                status=DocumentIndexStatus.DELETING,
                                file_exists=False)
    await it._index_document_async(_fake_task(), doc_id, "u1")
    assert await _status_of(env.factory, doc_id) == DocumentIndexStatus.DELETING


@pytest.mark.asyncio
async def test_worker_missing_file_on_indexing_doc_not_written_before_claim(env):
    """indexing 状态（已被其它 worker 领取）缺文件消息迟到：不写终态。"""
    doc_id, _ = await _seed_doc(env.factory,
                                status=DocumentIndexStatus.INDEXING,
                                file_exists=False)
    await it._index_document_async(_fake_task(), doc_id, "u1")
    assert await _status_of(env.factory, doc_id) == DocumentIndexStatus.INDEXING


# ---------- S02-c：Beat 补偿条件重置 ----------

@pytest.mark.asyncio
async def test_batch_does_not_touch_deleting_docs(env):
    """Beat 批量补偿：deleting 文档不被重置/投递。"""
    del_id, _ = await _seed_doc(env.factory,
                                status=DocumentIndexStatus.DELETING)
    f_id, _ = await _seed_doc(env.factory, tmp=env.tmp,
                              status=DocumentIndexStatus.INDEX_FAILED)
    await it._batch_index_pending_async(limit=10)
    submitted = {d[0] for d in env.delays}
    assert del_id not in submitted
    assert f_id in submitted
    assert await _status_of(env.factory, del_id) == DocumentIndexStatus.DELETING


# ---------- S04：条件发布结果传播 ----------

@pytest.mark.asyncio
async def test_sync_index_returns_actual_status_when_publish_rejected(env):
    """发布被拒（删除获胜置 deleting）时 _sync_index 返回真实状态，不是 indexed。"""
    doc_id, _ = await _seed_doc(env.factory, tmp=env.tmp)

    async def loader(path, md5=None, user_id=None):
        # 执行期间删除获胜
        async with env.factory() as s:
            await DocumentIndexRepository(s).update_status(
                doc_id, DocumentIndexStatus.DELETING)
            await s.commit()
        return [NS(page_content="甲", metadata={"source": path})]

    env.store.get_file_document = loader
    from app.services.document_index_service import _sync_index
    final = await _sync_index(doc_id, "u1")
    assert final != DocumentIndexStatus.INDEXED
    assert final == DocumentIndexStatus.DELETING
    assert await _status_of(env.factory, doc_id) == DocumentIndexStatus.DELETING


@pytest.mark.asyncio
async def test_upload_response_matches_db_when_publish_rejected(env, monkeypatch):
    """上传同步索引被删抢占：响应状态必须与 DB 一致，不得报 indexed 成功。"""
    import hashlib
    from app.services import document_index_service as dis
    monkeypatch.setattr(dis, "get_data_path", lambda: str(env.tmp))

    def _md5_sync(path):
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()

    monkeypatch.setattr(dis, "get_file_md5_hex_sync", _md5_sync)

    doc_box = {}

    async def loader(path, md5=None, user_id=None):
        # _sync_index claim 后、发布前，删除获胜
        async with env.factory() as s:
            await DocumentIndexRepository(s).update_status(
                doc_box["id"], DocumentIndexStatus.DELETING)
            await s.commit()
        return [NS(page_content="甲", metadata={"source": path})]

    env.store.get_file_document = loader

    # 截获记录创建，拿到 doc id
    orig_create = DocumentIndexRepository.create

    async def spy_create(self, *a, **k):
        doc = await orig_create(self, *a, **k)
        doc_box["id"] = doc.id
        return doc

    monkeypatch.setattr(DocumentIndexRepository, "create", spy_create)

    class _Up:
        filename = "race.txt"

        async def read(self):
            return b"hello"

        async def seek(self, pos):
            return None

    import app.utils.factory as fmod

    class LocalEmbed:
        pass

    monkeypatch.setattr(fmod, "embed_model", NS(resolve=lambda: LocalEmbed()))
    dis._embedding_health_cache.update({"available": None, "checked_at": 0})
    try:
        out = await dis.save_uploaded_file(_Up(), "u-race2", space_id="")
    finally:
        dis._embedding_health_cache.update({"available": None, "checked_at": 0})

    assert out["status"] != "indexed"
    assert out["status"] == "deleting"


# ---------- S02-d：人工重试条件化（行为级：deleting 拒绝） ----------

@pytest.mark.asyncio
async def test_reindex_deleting_doc_rejected(env):
    """deleting 文档不允许人工重试改写状态。"""
    from app.services.document_index_service import reindex_document
    doc_id, _ = await _seed_doc(env.factory,
                                status=DocumentIndexStatus.DELETING,
                                tmp=env.tmp)
    res = await reindex_document(doc_id, "u1")
    assert res["success"] is False
    assert await _status_of(env.factory, doc_id) == DocumentIndexStatus.DELETING
    assert env.delays == []
