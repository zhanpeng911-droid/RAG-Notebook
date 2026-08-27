"""HybridRetriever 单元测试 —— 权重策略、过滤下推与融合降级。

conftest 把 langchain 系替换为 MagicMock（EmptyRetriever 继承会直接
崩），先恢复真实栈；Chroma 向量库用记录调用的桩实现替代。
"""
import pytest

from tests.helpers.unmock import RETRIEVAL_STACK, restore_real

restore_real(*RETRIEVAL_STACK)

from langchain_core.documents import Document  # noqa: E402
from langchain_core.retrievers import BaseRetriever  # noqa: E402
from langchain_classic.retrievers import EnsembleRetriever  # noqa: E402

from app.rag.retrievers.bm25_tokenizer import tokenize_for_bm25  # noqa: E402
from app.rag.retrievers.empty_retriever import EmptyRetriever  # noqa: E402
from app.rag.retrievers.hybrid_retriever import HybridRetriever  # noqa: E402


class StubVectorRetriever(BaseRetriever):
    """占位向量检索器：不产生结果，只为了满足类型。"""

    def _get_relevant_documents(self, query, *, run_manager=None):
        return []

    async def _aget_relevant_documents(self, query, *, run_manager=None):
        return []


class FakeVectorStore:
    def __init__(self, docs=None):
        self.docs = docs if docs is not None else ["Redis 持久化 everysec", "MySQL 索引 B+树"]
        self.get_calls = []
        self.retriever_kwargs = None

    def get(self, include=None, where=None):
        self.get_calls.append({"include": include, "where": where})
        return {
            "documents": list(self.docs),
            "metadatas": [{"user_id": "u1", "space_id": "s1"}] * len(self.docs),
        }

    def as_retriever(self, **kwargs):
        self.retriever_kwargs = kwargs
        return StubVectorRetriever()



class FakeBM25(StubVectorRetriever):
    """带 BM25 元数据的假检索器：记录构建参数并返回固定命中。"""

    def __init__(self, k=None, preprocess_func=None, docs=None):
        super().__init__()
        # pydantic v2 会对未声明字段做校验拦截，这里绕过只作纯数据挂载
        object.__setattr__(self, "k", k)
        object.__setattr__(self, "preprocess_func", preprocess_func)
        object.__setattr__(self, "docs", docs)


@pytest.fixture()
def bm25_spy(monkeypatch):
    """替换模块内 BM25Retriever.from_documents，捕获入参并返回 FakeBM25。"""
    import app.rag.retrievers.hybrid_retriever as mod

    box = {"calls": []}

    def factory(documents=None, k=None, preprocess_func=None):
        docs = [d.page_content for d in (documents or [])]
        meta = [(d.metadata or {}) for d in (documents or [])]
        packed = [{"content": c, "metadata": m} for c, m in zip(docs, meta)]
        r = FakeBM25(k=k, preprocess_func=preprocess_func, docs=packed)
        box["calls"].append({"k": k, "preprocess_func": preprocess_func,
                             "docs": r.docs})
        return r

    monkeypatch.setattr(mod, "BM25Retriever", type("BM25Spy", (), {
        "from_documents": staticmethod(factory),
    }))
    return box

# ---------- get_dynamic_weights：纯函数权重策略 ----------

@pytest.mark.asyncio
async def test_weights_no_query_defaults_half():
    assert await HybridRetriever.get_dynamic_weights(None) == [0.5, 0.5]
    assert await HybridRetriever.get_dynamic_weights("") == [0.5, 0.5]


@pytest.mark.asyncio
async def test_weights_long_query_favors_vector():
    w = await HybridRetriever.get_dynamic_weights("长" * 51)
    assert w == [0.7, 0.3]


@pytest.mark.asyncio
async def test_weights_short_query_favors_bm25():
    w = await HybridRetriever.get_dynamic_weights("Redis 持久化")
    assert w == [0.3, 0.7]


@pytest.mark.asyncio
async def test_weights_medium_chinese_query_stays_half():
    # 无空格中文：词数密度低，不触发额外调整
    w = await HybridRetriever.get_dynamic_weights("中" * 30)
    assert w == [0.5, 0.5]


@pytest.mark.asyncio
async def test_weights_word_density_boosts_bm25():
    # 中等长度但空格词密集：在 0.5/0.5 基础上向 BM25 偏移到 0.4/0.6
    w = await HybridRetriever.get_dynamic_weights(" ".join(["w"] * 12))
    assert w == [0.4, 0.6]


@pytest.mark.asyncio
async def test_weights_long_and_word_dense_query():
    # 长查询（55 字符）且词密集：0.7/0.3 基础上各偏移 0.1
    w = await HybridRetriever.get_dynamic_weights(" ".join(["word"] * 11))
    assert w == [0.6, 0.4]


# ---------- get_bm25_retriever：取数与过滤 ----------

@pytest.mark.asyncio
async def test_bm25_without_user_returns_none():
    assert await HybridRetriever(FakeVectorStore()).get_bm25_retriever(None) is None


@pytest.mark.asyncio
async def test_bm25_builds_with_user_filter_and_tokenizer(bm25_spy):
    store = FakeVectorStore()
    r = await HybridRetriever(store).get_bm25_retriever("u1", k=3)
    assert isinstance(r, FakeBM25)
    call = bm25_spy["calls"][0]
    assert call["k"] == 3
    assert call["preprocess_func"] is tokenize_for_bm25
    assert [d["content"] for d in call["docs"]][:2] == [
        "Redis 持久化 everysec", "MySQL 索引 B+树",
    ]
    req = store.get_calls[-1]
    assert set(req["include"]) == {"documents", "metadatas"}
    assert req["where"] == {"user_id": "u1"}


@pytest.mark.asyncio
async def test_bm25_space_filter_uses_and_expression():
    store = FakeVectorStore()
    await HybridRetriever(store).get_bm25_retriever("u1", space_id="s9", k=2)
    assert store.get_calls[-1]["where"] == {
        "$and": [{"user_id": "u1"}, {"space_id": "s9"}],
    }


@pytest.mark.asyncio
async def test_bm25_empty_corpus_returns_none():
    assert await HybridRetriever(FakeVectorStore(docs=[])).get_bm25_retriever(
        "u1", k=2
    ) is None


@pytest.mark.asyncio
async def test_bm25_missing_metadata_entry_defaults_empty_dict(bm25_spy):
    store = FakeVectorStore(docs=["只有文档没有元数据"])
    original_get = store.get

    def get(include=None, where=None):
        result = original_get(include=include, where=where)
        result["metadatas"] = []  # 少于 documents 数量
        return result

    store.get = get
    await HybridRetriever(store).get_bm25_retriever("u1", k=1)
    doc = bm25_spy["calls"][0]["docs"][0]
    assert doc["content"].startswith("只有文档")
    assert doc["metadata"] == {}



@pytest.mark.asyncio
async def test_get_all_documents_assembles_from_store():
    docs = await HybridRetriever(FakeVectorStore())._get_all_documents()
    assert [d.page_content[:5] for d in docs] == ["Redis", "MySQL"]
    assert all(d.metadata["user_id"] == "u1" for d in docs)

# ---------- get_retriever：组合与降级 ----------

@pytest.mark.asyncio
async def test_get_retriever_without_user_gives_empty_retriever():
    r = await HybridRetriever(FakeVectorStore()).get_retriever("q", None)
    assert isinstance(r, EmptyRetriever)
    assert r.invoke("anything") == []


@pytest.mark.asyncio
async def test_get_retriever_returns_ensemble_with_candidate_k():
    store = FakeVectorStore()
    r = await HybridRetriever(store).get_retriever(
        "如何配置 Redis 主从复制集群", "u1", candidate_k=7,
    )
    assert isinstance(r, EnsembleRetriever)
    # 候选 k 同时下推到向量路与 BM25 路
    assert store.retriever_kwargs["search_kwargs"]["k"] == 7
    assert store.retriever_kwargs["search_kwargs"]["filter"] == {"user_id": "u1"}
    bm25 = r.retrievers[1]
    assert bm25.k == 7
    assert abs(sum(r.weights) - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_get_retriever_space_filter_pushed_to_vector_path():
    store = FakeVectorStore()
    await HybridRetriever(store).get_retriever("查询内容补充说明", "u1",
                                               space_id="s2", candidate_k=4)
    assert store.retriever_kwargs["search_kwargs"]["filter"] == {
        "$and": [{"user_id": "u1"}, {"space_id": "s2"}],
    }


@pytest.mark.asyncio
async def test_get_retriever_falls_back_to_vector_only_when_no_docs():
    store = FakeVectorStore(docs=[])
    r = await HybridRetriever(store).get_retriever("q", "u1", candidate_k=5)
    assert isinstance(r, StubVectorRetriever)


def test_document_assembly_uses_store_content_and_metadata():
    store = FakeVectorStore()
    raw = store.get(include=["documents", "metadatas"], where={"user_id": "u1"})
    docs = [
        Document(page_content=c, metadata=m)
        for c, m in zip(raw["documents"], raw["metadatas"])
    ]
    assert docs[0].page_content.startswith("Redis")
    assert docs[0].metadata["user_id"] == "u1"
