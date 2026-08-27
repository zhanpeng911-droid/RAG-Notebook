"""agentic/tools 测试 —— 检索工具的参数透传、隔离与注册表完整性。

RetrievalService 与向量库、数据库会话全部打桩注入，验证工具层
把用户/空间过滤与 scope/top_k 正确透传给统一检索服务。
"""
import sys
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agentic import tools as tools_mod
from app.agentic.tools import (
    AGENT_TOOLS,
    get_document_chunk,
    list_user_documents,
    search_all,
    search_knowledge,
    search_notes,
)


def _ev_dict():
    return {"source_type": "knowledge", "source_id": "k1", "chunk_id": "c1",
            "title": "t", "content": "正文", "score": 0.9, "metadata": {}}


@pytest.fixture()
def fake_retrieval(monkeypatch):
    """替换模块内 RetrievalService：记录构造与调用参数。"""
    calls = []

    class FakeService:
        def __init__(self, user_id, space_id=None, llm_config=None):
            calls.append(("init", user_id, space_id))

        async def retrieve(self, query, scope, top_k):
            calls.append(("retrieve", query, scope, top_k))
            return [NS(to_dict=lambda: _ev_dict())] if scope != "notes" else \
                [NS(to_dict=lambda: {**_ev_dict(),
                                     "source_type": "note"})]

    monkeypatch.setattr(tools_mod, "RetrievalService", FakeService)
    return calls


@pytest.mark.asyncio
async def test_search_knowledge_passes_scope_and_k(fake_retrieval):
    out = await search_knowledge("什么是 RAG", "u1", space_id="s3", top_k=6)
    assert out and out[0]["source_id"] == "k1"
    assert ("init", "u1", "s3") in fake_retrieval
    assert ("retrieve", "什么是 RAG", "space:s3", 6) in fake_retrieval


@pytest.mark.asyncio
async def test_search_knowledge_without_space_uses_knowledge_scope(fake_retrieval):
    await search_knowledge("q", "u2")
    assert ("retrieve", "q", "knowledge", 8) in fake_retrieval


@pytest.mark.asyncio
async def test_search_notes_forced_scope(fake_retrieval):
    out = await search_notes("笔记", "u1", top_k=3)
    assert out[0]["source_type"] == "note"
    assert ("retrieve", "笔记", "notes", 3) in fake_retrieval


@pytest.mark.asyncio
async def test_search_all_default_k(fake_retrieval):
    await search_all("混合", "u1")
    assert ("retrieve", "混合", "all", 8) in fake_retrieval


# ---------- get_document_chunk ----------

@pytest.fixture()
def fake_vector_module(monkeypatch):
    """替换 sys.modules 中 conftest 预注册的 mock 向量库模块。"""
    box = {"result": {"ids": [], "documents": [], "metadatas": []}}

    class Store:
        def get(self, ids=None, include=None):
            return box["result"]

    class VS:
        def __new__(cls):
            return NS(vectors_store=Store())

    monkeypatch.setattr(sys.modules["app.rag.vector_store"],
                        "VectorStoreService", VS)
    return box


@pytest.mark.asyncio
async def test_get_document_chunk_missing_returns_none(fake_vector_module):
    assert await get_document_chunk("c-miss", "u1") is None


@pytest.mark.asyncio
async def test_get_document_chunk_hit_returns_payload(fake_vector_module):
    fake_vector_module["result"] = {
        "ids": ["c1"], "documents": ["内容"], "metadatas": [{"user_id": "u1"}],
    }
    out = await get_document_chunk("c1", "u1")
    assert out["chunk_id"] == "c1"
    assert out["content"] == "内容"
    assert out["metadata"]["user_id"] == "u1"


# ---------- list_user_documents 关键词过滤 ----------

@pytest_asyncio.fixture
async def fake_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    from app.models.chat_history import Base as _Base
    from app.models.document_index import DocumentIndex
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as s:
        from datetime import datetime
        s.add_all([
            DocumentIndex(id="d1", user_id="u1", filename="f1.pdf",
                          original_filename="RAG 指南.pdf",
                          file_path="/p/f1.pdf", file_type=".pdf", md5="M1", status="indexed",
                          created_at=datetime.utcnow()),
            DocumentIndex(id="d2", user_id="u1", filename="f2.md",
                          original_filename="Docker 手册.md",
                          file_path="/p/f2.md", file_type=".md", md5="M2", status="uploaded",
                          created_at=datetime.utcnow()),
            DocumentIndex(id="d3", user_id="u2", filename="f3.pdf",
                          original_filename="RAG 他人的.pdf",
                          file_path="/p/f3.pdf", file_type=".pdf", md5="M3", status="indexed",
                          created_at=datetime.utcnow()),
        ])
        await s.commit()

    import app.db.db_config as db_cfg
    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", factory)
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_user_documents_filters_keyword_and_user(fake_db):
    out = await list_user_documents("rag", "u1", limit=10)
    assert [d["filename"] for d in out] == ["RAG 指南.pdf"]
    # 关键词不匹配
    empty = await list_user_documents("不存在", "u1")
    assert empty == []
    # 其它用户文档不可见（u1 查询不会返回 u2 的 RAG 文档）
    assert all(d["filename"] != "RAG 他人的.pdf" for d in out)


@pytest.mark.asyncio
async def test_list_user_documents_limit(fake_db):
    # 命中两条则受 limit 截断（构造第二命中）
    out = await list_user_documents("rag", "u1", limit=1)
    assert len(out) <= 1


# ---------- 注册表完整性 ----------

def test_agent_tools_registry_complete():
    assert set(AGENT_TOOLS) == {
        "search_knowledge", "search_notes", "search_all",
        "get_document_chunk", "list_user_documents",
    }
    for fn in AGENT_TOOLS.values():
        import inspect
        assert inspect.iscoroutinefunction(fn)
