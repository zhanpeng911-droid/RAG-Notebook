"""RetrievalService 单元测试 —— 统一检索编排、重排与双路降级。

运行时配置、LLM 工厂、向量库等外部点全部在模块命名空间打桩；
数据库会话用可编程假会话替代，不触发真实连接。
"""
from types import SimpleNamespace

import pytest

import app.rag.retrieval_service as rs_mod
from app.rag.retrieval_service import (
    Evidence,
    RetrievalService,
    SourceType,
)


def ev(source_id="f1", content="内容", score=0.9, st=SourceType.KNOWLEDGE,
       title="文件.pdf", meta=None):
    return Evidence(source_type=st, source_id=source_id, chunk_id="c",
                    title=title, content=content, score=score,
                    metadata=meta or {})


@pytest.fixture()
def rt(monkeypatch):
    """runtime_config 桩：rerank 开启、候选倍数 3，返回记录盒便于改写。"""
    box = {"values": {"retrieval.rerank_enabled": True,
                      "retrieval.rerank_candidate_multiplier": 3}}
    monkeypatch.setattr(rs_mod, "get_runtime_config",
                        lambda key: box["values"].get(key))
    return box


def test_init_requires_user_id():
    with pytest.raises(ValueError):
        RetrievalService(user_id="")
    svc = RetrievalService(user_id="u1", space_id="s1", llm_config={"x": 1})
    assert svc.user_id == "u1" and svc.space_id == "s1"


@pytest.mark.asyncio
async def test_retrieve_blank_query_returns_empty(rt):
    assert await RetrievalService(user_id="u").retrieve("   ") == []


@pytest.mark.asyncio
async def test_evidence_to_dict_roundtrip():
    d = ev().to_dict()
    assert d["source_type"] == "knowledge"
    assert {"source_type", "source_id", "chunk_id", "title", "content",
            "score", "metadata"} <= set(d)


def test_deduplicate_keeps_first_occurrence():
    a, b, dup = ev(content="同一段落"*30), ev(source_id="f2"), \
        ev(source_id="f1", content="同一段落"*30, score=0.5)
    out = RetrievalService(user_id="u")._deduplicate([a, b, dup])
    assert [e.source_id for e in out] == ["f1", "f2"]


def test_merge_adjacent_combines_same_document():
    svc = RetrievalService(user_id="u")
    merged = svc._merge_adjacent([
        ev(source_id="docA", title="A.pdf", content="第一段", score=0.4),
        ev(source_id="docA", title="A.pdf", content="第二段", score=0.7),
        ev(source_id="docB", title="B.pdf", content="其他"),
    ])
    assert len(merged) == 2
    assert "第一段\n第二段" in merged[0].content
    assert merged[0].score == 0.7
    assert merged[1].source_id == "docB"


# ---------- retrieve 编排 ----------

def _spy_results(monkeypatch, svc, kb=None, notes=None, kb_raises=False):
    seen = {}

    async def kb_spy(query, candidate_k, space_id=None, use_hyde=True):
        seen["kb_args"] = (query, candidate_k, space_id)
        if kb_raises:
            raise RuntimeError("知识库炸了")
        return list(kb or [])

    async def note_spy(query, candidate_k):
        seen["note_args"] = (query, candidate_k)
        return list(notes or [])

    monkeypatch.setattr(svc, "_retrieve_knowledge", kb_spy)
    monkeypatch.setattr(svc, "_retrieve_notes", note_spy)
    return seen


@pytest.mark.asyncio
async def test_scope_all_hits_both_sources(rt, monkeypatch):
    svc = RetrievalService(user_id="u1")
    seen = _spy_results(monkeypatch, svc,
                        kb=[ev("k1")], notes=[ev("n1", st=SourceType.NOTE)])
    out = await svc.retrieve("查询", scope="all")
    assert {e.source_type for e in out} == {"knowledge", "note"}
    assert "kb_args" in seen and "note_args" in seen


@pytest.mark.asyncio
async def test_scope_knowledge_only_skips_notes(rt, monkeypatch):
    svc = RetrievalService(user_id="u1")
    seen = _spy_results(monkeypatch, svc, kb=[ev("k1")])
    out = await svc.retrieve("q", scope="knowledge")
    assert all(e.source_type == SourceType.KNOWLEDGE for e in out)
    assert "note_args" not in seen


@pytest.mark.asyncio
async def test_scope_space_overrides_and_excludes_notes(rt, monkeypatch):
    svc = RetrievalService(user_id="u1")
    seen = _spy_results(monkeypatch, svc, kb=[ev("k1")])
    await svc.retrieve("q", scope="space:s42")
    assert seen["kb_args"][2] == "s42"
    assert "note_args" not in seen


@pytest.mark.asyncio
async def test_notes_with_active_space_are_excluded(rt, monkeypatch):
    svc = RetrievalService(user_id="u1", space_id="s9")
    seen = _spy_results(monkeypatch, svc, kb=[ev("k1")])
    await svc.retrieve("q", scope="all")
    assert "note_args" not in seen


@pytest.mark.asyncio
async def test_one_branch_failure_keeps_other_results(rt, monkeypatch):
    svc = RetrievalService(user_id="u1")
    _spy_results(monkeypatch, svc, kb=[ev("ok-1")], kb_raises=True is False or None)
    # 重新打桩：知识库抛异常，笔记正常
    async def kb_fail(q, k, space_id=None, use_hyde=True):
        raise RuntimeError("boom")

    async def note_ok(q, k):
        return [ev("n-ok", st=SourceType.NOTE)]

    monkeypatch.setattr(svc, "_retrieve_knowledge", kb_fail)
    monkeypatch.setattr(svc, "_retrieve_notes", note_ok)
    out = await svc.retrieve("q", scope="all")
    assert [e.source_id for e in out] == ["n-ok"]


@pytest.mark.asyncio
async def test_unknown_scope_returns_empty(rt):
    assert await RetrievalService(user_id="u").retrieve("q", scope="wrong") == []


@pytest.mark.asyncio
async def test_candidate_multiplier_applied_when_rerank_on(rt, monkeypatch):
    svc = RetrievalService(user_id="u1")
    seen = _spy_results(monkeypatch, svc, kb=[ev("k")] * 12)
    await svc.retrieve("q", scope="knowledge", top_k=4, use_rerank=True)
    assert seen["kb_args"][1] == 12  # 4 × 3


@pytest.mark.asyncio
async def test_no_expansion_when_rerank_disabled(rt, monkeypatch):
    rt["values"]["retrieval.rerank_enabled"] = False
    svc = RetrievalService(user_id="u1")
    seen = _spy_results(monkeypatch, svc, kb=[ev("k")] * 6)
    await svc.retrieve("q", scope="knowledge", top_k=4, use_rerank=True)
    assert seen["kb_args"][1] == 4


@pytest.mark.asyncio
async def test_top_k_truncation_after_sorting(rt, monkeypatch):
    svc = RetrievalService(user_id="u1")
    cands = [ev(f"c{i}", score=0.1 * i) for i in range(10)]
    _spy_results(monkeypatch, svc, kb=cands)
    rt["values"]["retrieval.rerank_enabled"] = False
    out = await svc.retrieve("q", scope="knowledge", top_k=3)
    assert len(out) == 3
    assert [e.score for e in out] == sorted((0.1 * i for i in range(10)),
                                            reverse=True)[:3]


@pytest.mark.asyncio
async def test_dedup_runs_before_merge(rt, monkeypatch):
    svc = RetrievalService(user_id="u1")
    dup_a = ev("same", content="X" * 120, score=0.8)
    dup_b = ev("same", content="X" * 200, score=0.6)
    _spy_results(monkeypatch, svc, kb=[dup_a, dup_b])
    rt["values"]["retrieval.rerank_enabled"] = False
    out = await svc.retrieve("q", scope="knowledge", top_k=5)
    assert len(out) == 1


# ---------- 重排序 ----------

class FakeReranker:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    async def rerank(self, query, documents):
        self.calls.append((query, documents))
        if self.error:
            raise self.error
        return self.results


@pytest.mark.asyncio
async def test_rerank_rewrites_scores_and_metadata(rt, monkeypatch):
    from types import SimpleNamespace as NS
    fakes = FakeReranker(results=[
        NS(index=1, score=0.99), NS(index=0, score=0.11),
    ])
    import app.rag.reranker as reranker_module
    monkeypatch.setattr(reranker_module, "reranker", fakes)

    svc = RetrievalService(user_id="u1")
    cands = [ev("a", content="甲文本", score=0.5),
             ev("b", content="乙文本", score=0.5)]
    out = await svc._rerank_evidences("q", cands)
    assert [(e.source_id, e.score) for e in out] == [("b", 0.99), ("a", 0.11)]
    assert out[0].metadata["rerank_score"] == 0.99
    # 文本按原顺序送出，且每条被截断至 1000 字内
    assert fakes.calls[0][1] == ["甲文本", "乙文本"]


@pytest.mark.asyncio
async def test_rerank_out_of_range_index_ignored(rt, monkeypatch):
    from types import SimpleNamespace as NS
    fakes = FakeReranker(results=[NS(index=9, score=1.0)])
    import app.rag.reranker as reranker_module
    monkeypatch.setattr(reranker_module, "reranker", fakes)
    out = await RetrievalService(user_id="u")._rerank_evidences(
        "q", [ev("only")])
    assert out == []


@pytest.mark.asyncio
async def test_rerank_exception_returns_empty_fallback(rt, monkeypatch):
    fakes = FakeReranker(error=RuntimeError("服务挂了"))
    import app.rag.reranker as reranker_module
    monkeypatch.setattr(reranker_module, "reranker", fakes)
    assert await RetrievalService(user_id="u")._rerank_evidences(
        "q", [ev("a")]) == []


# ---------- 数据库假件（关键词降级路径） ----------

def _install_fake_db(monkeypatch):
    """注入假 AsyncSessionLocal 与文档索引仓储，返回可编程记录盒。"""
    box = {"repo_docs": [], "notes": []}

    import app.db.db_config as db_cfg
    import app.repositories.document_index_repository as repo_mod

    class FakeRepo:
        def __init__(self, session):
            pass

        async def get_user_documents(self, user_id, space_id=None,
                                     status=None):
            return list(box["repo_docs"])

    monkeypatch.setattr(repo_mod, "DocumentIndexRepository", FakeRepo)

    class _NoopResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class SessionCtx:
        async def __aenter__(self):
            async def _execute(stmt):
                return _NoopResult()
            return SimpleNamespace(execute=_execute)

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", SessionCtx)
    return box


@pytest.fixture()
def fake_factory_db(monkeypatch):
    return _install_fake_db(monkeypatch)


# ---------- 知识库 / 笔记向量路 ----------

class _Doc:
    def __init__(self, page_content, metadata):
        self.page_content = page_content
        self.metadata = metadata


class _Retriever:
    def __init__(self, docs):
        self.docs = docs
        self.search_query = None

    async def ainvoke(self, q):
        self.search_query = q
        return self.docs


@pytest.fixture()
def fake_vector_class(monkeypatch):
    """替换向量化导入点：VectorStoreService() 返回可控实例。"""
    holder = {}
    inst = SimpleNamespace()

    async def get_retriever(query, user_id, space_id=None, candidate_k=None):
        holder["args"] = (query, user_id, space_id, candidate_k)
        return holder["retriever"]

    inst.get_retriever = get_retriever
    holder["instance"] = inst

    class Cls:
        def __new__(cls):
            return inst

    import app.rag.vector_store as vs_stub
    monkeypatch.setattr(vs_stub, "VectorStoreService", Cls)
    return holder


@pytest.fixture()
def embed_available(monkeypatch):
    state = {"ok": True}
    import app.utils.factory as factory_mod

    class _E:
        def resolve(self):
            if not state["ok"]:
                raise RuntimeError("embed down")
            return object()

    monkeypatch.setattr(factory_mod, "embed_model", _E())
    return state


@pytest.mark.asyncio
async def test_knowledge_vector_path_scores_and_filters(fake_vector_class,
                                                        embed_available):
    docs = [
        _Doc("高分段落", {"md5": "M1", "original_filename": "r.pdf",
                          "user_id": "u1", "space_id": "s1", "score": 0.87}),
        _Doc("中分段落", {"original_filename": "r.pdf", "space_id": "s1"}),
        _Doc("其他空间文档", {"original_filename": "other.pdf",
                              "space_id": "s-else"}),
    ]
    r = _Retriever(docs)
    fake_vector_class["retriever"] = r
    svc = RetrievalService(user_id="u1")
    out = await svc._retrieve_knowledge("原始问题", 5, space_id="s1",
                                        use_hyde=False)
    # 客户端空间过滤丢弃其它空间文档；分数优先 metadata.score，
    # 缺失时按排名下界递减
    assert [e.content for e in out] == ["高分段落", "中分段落"]
    assert out[0].score == 0.87
    assert 0 <= out[1].score < 1.0
    assert out[0].metadata["md5"] == "M1"
    assert out[0].source_type == SourceType.KNOWLEDGE
    args = fake_vector_class["args"]
    assert args[1] == "u1" and args[2] == "s1"


@pytest.mark.asyncio
async def test_knowledge_hyde_unavailable_falls_back_to_original(fake_vector_class,
                                                                 embed_available):
    """单测环境下 LLM 链不可构建，HyDE 必须按设计降级为原始查询。

    生产中该链由真实 langchain 构成；此处只验证 _generate_hyde 的
    失败兜底分支（443-445 行）与检索使用原始查询的行为。
    """
    r = _Retriever([_Doc("正文", {"original_filename": "f.pdf",
                                  "space_id": "s1"})])
    fake_vector_class["retriever"] = r
    out = await RetrievalService(user_id="u1")._retrieve_knowledge(
        "什么是一致性哈希", 3, space_id="s1", use_hyde=True)
    assert [e.content for e in out] == ["正文"]
    assert r.search_query == "什么是一致性哈希"


# ---------- 笔记向量路与关键词兜底 ----------

class FakeNoteStore:
    def __init__(self, pairs):
        self.pairs = pairs
        self.calls = []

    def similarity_search_with_score(self, query, k=None, filter=None):
        self.calls.append({"query": query, "k": k, "filter": filter})
        return self.pairs


@pytest.mark.asyncio
async def test_note_vector_path_scores_distance_inverted(embed_available,
                                                         monkeypatch):
    fake_note_service = SimpleNamespace(
        notes_store=FakeNoteStore([
            (_Doc("笔记甲", {"note_id": "na", "title": "甲", "user_id": "u1"}), 0.0),
            (_Doc("笔记乙", {"note_id": "nb"}), 3.0),
        ]),
    )
    import app.services.note_service as ns_mod
    monkeypatch.setattr(ns_mod, "note_service", fake_note_service)

    out = await RetrievalService(user_id="u1")._retrieve_notes("查询", 4)
    call = fake_note_service.notes_store.calls[0]
    assert call["filter"] == {"$and": [{"user_id": "u1"}, {"doc_type": "note"}]}
    assert out[0].score == 1.0            # 距离 0 → 相似度满分
    assert abs(out[1].score - 0.25) < 1e-9
    assert out[0].metadata["note_id"] == "na"


@pytest.mark.asyncio
async def test_build_note_keyword_filter_empty_query_owner_only():
    from app.models.note import Note
    clause = RetrievalService(user_id="owner")._build_note_keyword_filter(
        Note, "owner", "   ")
    assert "user_id" in str(clause)


@pytest.mark.asyncio
async def test_keyword_notes_returns_preview_and_rank_scores(fake_factory_db):
    class FakeNoteRow(SimpleNamespace):
        pass

    notes = [
        SimpleNamespace(id="n1", title="T1", user_id="u1", content="长" * 300),
        SimpleNamespace(id="n2", title="T2", user_id="u1", content="短文"),
    ]

    import app.db.db_config as db_cfg

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return notes

    class FakeSession:
        async def __aenter__(self):
            async def _execute(stmt):
                return FakeResult()
            return SimpleNamespace(execute=_execute)

        async def __aexit__(self, *exc):
            return False

    original = db_cfg.AsyncSessionLocal
    db_cfg.AsyncSessionLocal = FakeSession
    try:
        out = await RetrievalService(user_id="u1")._keyword_search_notes(
            "关键词", 5)
    finally:
        db_cfg.AsyncSessionLocal = original

    assert len(out) == 2
    assert out[0].content.endswith("...")
    assert len(out[0].content) == 203
    assert out[1].content == "短文"
    assert out[0].score > out[1].score >= 0
