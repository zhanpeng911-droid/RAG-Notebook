"""note_service 直接单测 —— CRUD、搜索降级、关联推荐、自动标签、补全、SSE、统计、导出。

内存 SQLite + 真实 NoteRepository；NoteVectorIndex 打桩。直接调用服务方法，
规避 aiosqlite await 后 coverage 追踪缺陷。
"""
import json
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.models.chat_history import Base
from app.schemas.models import NoteCreate, NoteUpdate
from app.services.note_service import NoteService, _get_next_interval

USER_A = "u-aaaa-0000-0000-000000000001"
USER_B = "u-bbbb-0000-0000-000000000002"


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=engine, class_=AsyncSession,
                             expire_on_commit=False)
    yield fac
    await engine.dispose()


async def _seed(fac, user_id=USER_A, **kw):
    async with fac() as s:
        note = Note(id=kw.get("id", "n-1"), user_id=user_id,
                    title=kw.get("title", "笔记"), content=kw.get("content", "内容"),
                    category=kw.get("category", "study"), tags=kw.get("tags"))
        s.add(note)
        await s.commit()


async def _no_cache(p, m):
    """get_cached_llm_response 是 async，返回 None 表示未命中。"""
    return None


async def _no_set(p, m, v):
    """set_cached_llm_response 是 async 且 3 个参数。"""
    return None


def _cached(v):
    """返回 async getter 桩。"""
    async def _f(p, m):
        return v
    return _f


class _FakeIndex:
    """NoteVectorIndex 替身 —— 属性可被 monkeypatch.setattr 直接覆盖。"""
    store = "chroma"
    search_user_notes = None
    search_related_notes = None
    find_related_for_note_content = None


def _svc(monkeypatch):
    svc = NoteService()
    monkeypatch.setattr(svc, "note_index", _FakeIndex())
    monkeypatch.setattr(svc, "note_repo", NoteService().note_repo)
    return svc


# ---------- 工具函数 ----------

def test_get_next_interval():
    assert _get_next_interval(0) == 1
    assert _get_next_interval(5) == 30
    assert _get_next_interval(9) == 30


def test_notes_store_property(monkeypatch):
    svc = _svc(monkeypatch)
    assert svc.notes_store == "chroma"


def test_extract_json():
    assert NoteService._extract_json("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert NoteService._extract_json("前置 {\"a\": 1} 后置") == '{"a": 1}'
    assert NoteService._extract_json("no json") == "no json"


# ---------- CRUD ----------

@pytest.mark.asyncio
async def test_create_note(factory, monkeypatch):
    svc = _svc(monkeypatch)
    async with factory() as db:
        resp = await svc.create_note(db, USER_A, NoteCreate(title="新笔记",
                                                            content="正文"))
        assert resp.title == "新笔记"
    async with factory() as db:
        review = (await db.execute(
            __import__("sqlalchemy").select(ReviewRecord))).scalars().all()
        assert len(review) == 1


@pytest.mark.asyncio
async def test_update_note(factory, monkeypatch):
    await _seed(factory)
    svc = _svc(monkeypatch)
    async with factory() as db:
        resp = await svc.update_note(db, "n-1", USER_A,
                                     NoteUpdate(title="改名", content="新内容",
                                                tags=["a"], category="life"))
        assert resp.title == "改名"
        assert resp.category == "life"
        assert resp.tags == ["a"]


@pytest.mark.asyncio
async def test_update_note_not_found(factory, monkeypatch):
    svc = _svc(monkeypatch)
    async with factory() as db:
        assert await svc.update_note(db, "n-x", USER_A, NoteUpdate(title="x")) is None


@pytest.mark.asyncio
async def test_delete_note(factory, monkeypatch):
    await _seed(factory)
    svc = _svc(monkeypatch)
    async with factory() as db:
        assert await svc.delete_note(db, "n-1", USER_A) is True
        assert await svc.delete_note(db, "n-1", USER_A) is False


@pytest.mark.asyncio
async def test_get_note_own_and_missing(factory, monkeypatch):
    await _seed(factory)
    svc = _svc(monkeypatch)
    async with factory() as db:
        assert (await svc.get_note(db, "n-1", USER_A)).id == "n-1"
        assert await svc.get_note(db, "n-1", USER_B) is None
        assert await svc.get_note(db, "n-x", USER_A) is None


@pytest.mark.asyncio
async def test_list_notes_paged_and_tag(factory, monkeypatch):
    await _seed(factory, id="n-1", title="一", tags=["work"])
    await _seed(factory, id="n-2", title="二", tags=["life"], category="life")
    svc = _svc(monkeypatch)
    async with factory() as db:
        notes, total = await svc.list_notes(db, USER_A)
        assert total == 2
        notes, total = await svc.list_notes(db, USER_A, category="life")
        assert total == 1
        notes, total = await svc.list_notes(db, USER_A, tag="work")
        assert total == 1
        assert notes[0].id == "n-1"


# ---------- 搜索 ----------

@pytest.mark.asyncio
async def test_search_notes_vector_hit(factory, monkeypatch):
    await _seed(factory, id="n-1", title="命中")
    svc = _svc(monkeypatch)
    svc.note_index.search_user_notes = lambda q, u, k: ["n-1", "n-missing"]
    async with factory() as db:
        out = await svc.search_notes(db, USER_A, "q")
        assert [n.id for n in out] == ["n-1"]


@pytest.mark.asyncio
async def test_search_notes_vector_exception_like_fallback(factory, monkeypatch):
    await _seed(factory, id="n-1", title="目标词")
    svc = _svc(monkeypatch)

    def _boom(q, u, k):
        raise RuntimeError("chroma down")
    svc.note_index.search_user_notes = _boom
    async with factory() as db:
        out = await svc.search_notes(db, USER_A, "目标词")
        assert len(out) >= 1


@pytest.mark.asyncio
async def test_search_related_notes(factory, monkeypatch):
    svc = _svc(monkeypatch)
    docs = [(NS(metadata={"note_id": "n-1", "title": "t"}, page_content="内容"), 0.2)]
    svc.note_index.search_related_notes = lambda q, u, k: docs
    out = await svc.search_related_notes("q", USER_A)
    assert out[0]["note_id"] == "n-1"
    assert out[0]["similarity"] == pytest.approx(0.8, abs=0.01)


@pytest.mark.asyncio
async def test_search_related_notes_exception(factory, monkeypatch):
    svc = _svc(monkeypatch)

    def _boom(q, u, k):
        raise RuntimeError("down")
    svc.note_index.search_related_notes = _boom
    assert await svc.search_related_notes("q", USER_A) == []


@pytest.mark.asyncio
async def test_get_related_notes_note_missing(factory, monkeypatch):
    svc = _svc(monkeypatch)
    async with factory() as db:
        out = await svc.get_related_notes(db, "n-x", USER_A)
        assert out == {"notes": [], "knowledge_docs": []}


@pytest.mark.asyncio
async def test_get_related_notes_success(factory, monkeypatch):
    await _seed(factory, id="n-1", content="正文内容")
    svc = _svc(monkeypatch)
    note_docs = [(NS(metadata={"note_id": "n-2", "title": "相关"}, page_content="x"), 0.1)]
    kb_docs = [(NS(metadata={"source": "kb1", "original_filename": "文档"}, page_content="y"), 0.3)]
    svc.note_index.find_related_for_note_content = lambda c, u, nid, k: note_docs

    from app.rag import vector_store as vs_mod
    fake_vs = NS(vectors_store=NS(similarity_search_with_score=lambda q, k=3, filter=None: kb_docs))
    monkeypatch.setattr(vs_mod, "VectorStoreService", lambda: fake_vs)

    async with factory() as db:
        out = await svc.get_related_notes(db, "n-1", USER_A)
        assert out["notes"][0]["id"] == "n-2"
        assert out["knowledge_docs"][0]["id"] == "kb1"


# ---------- 回顾记录 ----------

@pytest.mark.asyncio
async def test_ensure_review_record(factory, monkeypatch):
    await _seed(factory, id="n-1")
    svc = _svc(monkeypatch)
    async with factory() as db:
        assert await svc.ensure_review_record(db, "n-1", USER_A) is True
        assert await svc.ensure_review_record(db, "n-1", USER_A) is False


# ---------- 自动标签 ----------

class _FakeModel:
    def __init__(self, content="", chunks=None):
        self.content = content
        self._chunks = chunks

    async def ainvoke(self, msgs):
        return NS(content=self.content)

    async def astream(self, msgs):
        for c in self._chunks or []:
            yield NS(content=c)


def _stub_factory(monkeypatch, model=None, usable=False):
    import app.utils.factory as f
    monkeypatch.setattr(f, "llm_config_is_usable", lambda cfg: usable)
    monkeypatch.setattr(f, "sanitize_client_llm_config", lambda cfg: cfg)
    monkeypatch.setattr(f, "chat_model", model or _FakeModel())
    monkeypatch.setattr(f, "create_chat_model_from_config", lambda cfg: model or _FakeModel())


@pytest.mark.asyncio
async def test_auto_tag_success(factory, monkeypatch):
    await _seed(factory, id="n-1")
    svc = _svc(monkeypatch)
    _stub_factory(monkeypatch, model=_FakeModel(content=json.dumps({"tags": ["a"], "category": "work"})))
    import app.db.db_config as dbc
    monkeypatch.setattr(dbc, "AsyncSessionLocal", factory)
    from app.cache import llm_cache as lc
    monkeypatch.setattr(lc, "get_cached_llm_response", NS())
    monkeypatch.setattr(lc, "set_cached_llm_response", NS())
    await svc._auto_tag_and_review("n-1", USER_A, "内容")


@pytest.mark.asyncio
async def test_auto_tag_cache_hit(factory, monkeypatch):
    await _seed(factory, id="n-1")
    svc = _svc(monkeypatch)
    _stub_factory(monkeypatch)
    import app.db.db_config as dbc
    monkeypatch.setattr(dbc, "AsyncSessionLocal", factory)
    from app.cache import llm_cache as lc
    monkeypatch.setattr(lc, "get_cached_llm_response",
                        lambda p, m: json.dumps({"tags": [], "category": "life"}))
    await svc._auto_tag_and_review("n-1", USER_A, "内容")


@pytest.mark.asyncio
async def test_auto_tag_json_error(factory, monkeypatch):
    svc = _svc(monkeypatch)
    _stub_factory(monkeypatch, model=_FakeModel(content="not-json"))
    from app.cache import llm_cache as lc
    monkeypatch.setattr(lc, "get_cached_llm_response", lambda p, m: None)
    await svc._auto_tag_and_review("n-1", USER_A, "内容")  # 不应抛出


# ---------- 补全 ----------

@pytest.mark.asyncio
async def test_autocomplete_success(monkeypatch):
    svc = _svc(monkeypatch)
    _stub_factory(monkeypatch, model=_FakeModel(content="续写内容"))
    from app.cache import llm_cache as lc
    monkeypatch.setattr(lc, "get_cached_llm_response", _no_cache)
    monkeypatch.setattr(lc, "set_cached_llm_response", _no_set)
    out = await svc.autocomplete("写")
    assert out["success"] is True
    assert out["completion"] == "续写内容"


@pytest.mark.asyncio
async def test_autocomplete_cache_hit(monkeypatch):
    svc = _svc(monkeypatch)
    _stub_factory(monkeypatch)
    from app.cache import llm_cache as lc
    monkeypatch.setattr(lc, "get_cached_llm_response", _cached("缓存补全"))
    out = await svc.autocomplete("写")
    assert out["completion"] == "缓存补全"


@pytest.mark.asyncio
async def test_autocomplete_dedup(monkeypatch):
    # completion 前 10 字符是 context 后缀时，剥离重复前缀
    ctx = "abcdefghij"
    svc = _svc(monkeypatch)
    model = _FakeModel(content="abcdefghij续写")
    _stub_factory(monkeypatch, model=model)
    from app.cache import llm_cache as lc
    monkeypatch.setattr(lc, "get_cached_llm_response", _no_cache)
    monkeypatch.setattr(lc, "set_cached_llm_response", _no_set)
    out = await svc.autocomplete(ctx)
    assert out["completion"] == "续写"


@pytest.mark.asyncio
async def test_autocomplete_error(monkeypatch):
    svc = _svc(monkeypatch)

    def _boom(cfg):
        raise RuntimeError("模型挂")
    import app.utils.factory as f
    monkeypatch.setattr(f, "llm_config_is_usable", _boom)
    out = await svc.autocomplete("写")
    assert out == {"success": False, "completion": ""}


# ---------- 写作辅助 SSE ----------

@pytest.mark.asyncio
async def test_assist_stream_success(monkeypatch):
    svc = _svc(monkeypatch)
    _stub_factory(monkeypatch, model=_FakeModel(chunks=["块一", "", "块二"]))
    out = [e async for e in svc.assist_stream("正文", "continue")]
    assert "块一" in out[0]
    assert "[DONE]" in out[-1]
    assert len(out) == 3  # 空 chunk 被跳过


@pytest.mark.asyncio
async def test_assist_stream_error(monkeypatch):
    svc = _svc(monkeypatch)

    class _BoomModel:
        async def astream(self, msgs):
            raise RuntimeError("流挂了")
            yield  # pragma: no cover
    _stub_factory(monkeypatch, model=_BoomModel())
    out = [e async for e in svc.assist_stream("正文", "continue")]
    assert "[ERROR" in out[0]


# ---------- 统计 / 导出 ----------

@pytest.mark.asyncio
async def test_category_stats(factory, monkeypatch):
    await _seed(factory, id="n-1", category="work")
    await _seed(factory, id="n-2", category="study")
    svc = _svc(monkeypatch)
    async with factory() as db:
        out = await svc.get_category_stats(db, USER_A)
        assert out["total"] == 2
        assert out["uncategorized"] == 0
        cats = {c["category"]: c["count"] for c in out["categories"]}
        assert cats["work"] == 1 and cats["study"] == 1


@pytest.mark.asyncio
async def test_export_markdown(factory, monkeypatch):
    await _seed(factory, id="n-1", title="标题", tags=["a", "b"], category="study")
    svc = _svc(monkeypatch)
    async with factory() as db:
        md = await svc.export_note_markdown(db, "n-1", USER_A)
        assert "title: 标题" in md
        assert "tags: [a, b]" in md
        assert "# 标题" in md
        assert await svc.export_note_markdown(db, "n-x", USER_A) is None


def test_get_note_service_factory():
    from app.services.note_service import get_note_service
    assert isinstance(get_note_service(), NoteService)
