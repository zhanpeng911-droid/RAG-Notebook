"""note_vector_index 直接单测 —— Chroma notes_collection 封装、过滤、自愈重试。

Chroma 打桩为可记录调用的假实现，覆盖增删改查、搜索过滤与索引过期自愈。
"""
from types import SimpleNamespace as NS

import pytest

from app.services.note_vector_index import (
    NoteVectorIndex, _user_note_filter, _looks_like_stale_client_error,
    NOTES_COLLECTION_NAME,
)

USER_A = "u-aaaa-0000-0000-000000000001"


class FakeChroma:
    """记录调用的 Chroma 替身。"""

    instances = []

    def __init__(self, collection_name=None, embedding_function=None,
                 persist_directory=None):
        self.collection_name = collection_name
        self.calls = []
        FakeChroma.instances.append(self)

    def add_documents(self, documents, ids=None):
        self.calls.append(("add", documents, ids))

    def delete(self, where=None):
        self.calls.append(("delete", where))

    def similarity_search(self, query, k=None, filter=None):
        self.calls.append(("similarity_search", query, k, filter))
        return [NS(metadata={"note_id": "n1"}, page_content="x")]

    def similarity_search_with_score(self, query, k=None, filter=None):
        self.calls.append(("similarity_search_with_score", query, k, filter))
        return [(NS(metadata={"note_id": "n1"}, page_content="x"), 0.1)]


class FakeDocument:
    """捕获 Document 构造参数的替身（conftest 把 langchain Document mock 了）。"""
    captured = []

    def __init__(self, **kwargs):
        FakeDocument.captured.append(kwargs)
        self.__dict__.update(kwargs)


@pytest.fixture(autouse=True)
def _patch_chroma(monkeypatch):
    FakeChroma.instances = []
    FakeDocument.captured = []
    import app.services.note_vector_index as m
    monkeypatch.setattr(m, "Chroma", FakeChroma)
    monkeypatch.setattr(m, "Document", FakeDocument)
    yield


def test_user_note_filter():
    assert _user_note_filter("u1") == {
        "$and": [{"user_id": "u1"}, {"doc_type": "note"}]}


def test_stale_client_error_detect():
    assert _looks_like_stale_client_error(Exception("Error finding id")) is True
    assert _looks_like_stale_client_error(Exception("internal error")) is True
    assert _looks_like_stale_client_error(Exception("正常错误")) is False


def test_ensure_store_creates_once():
    idx = NoteVectorIndex()
    s1 = idx._ensure_store()
    s2 = idx._ensure_store()
    assert s1 is s2
    assert len(FakeChroma.instances) == 1
    assert s1.collection_name == NOTES_COLLECTION_NAME


def test_store_property():
    idx = NoteVectorIndex()
    assert idx.store is FakeChroma.instances[0]


def test_reset_store_clears_cache():
    # SharedSystemClient 由 conftest mock，_reset_store 内部调用即可覆盖该分支
    idx = NoteVectorIndex()
    idx._ensure_store()
    idx._reset_store()
    assert idx._store is None


def test_note_document_metadata():
    NoteVectorIndex._note_document("n1", USER_A, "标题", "内容")
    kwargs = FakeDocument.captured[-1]
    assert kwargs["page_content"] == "内容"
    meta = kwargs["metadata"]
    assert meta["user_id"] == USER_A
    assert meta["note_id"] == "n1"
    assert meta["doc_type"] == "note"
    assert meta["title"] == "标题"


def test_add_note():
    idx = NoteVectorIndex()
    idx.add_note("n1", USER_A, "标题", "内容")
    store = FakeChroma.instances[0]
    assert store.calls[0][0] == "add"
    assert store.calls[0][2] == ["n1"]
    assert store.calls[0][1][0].metadata["user_id"] == USER_A


def test_upsert_note():
    idx = NoteVectorIndex()
    idx.upsert_note("n1", USER_A, "标题", "内容")
    store = FakeChroma.instances[0]
    assert store.calls[0][0] == "delete"
    assert store.calls[1][0] == "add"


def test_update_note_wraps_upsert():
    idx = NoteVectorIndex()
    idx.update_note("n1", USER_A, "标题", "内容")
    store = FakeChroma.instances[0]
    assert store.calls[0][0] == "delete"
    assert store.calls[1][0] == "add"


def test_delete_note():
    idx = NoteVectorIndex()
    idx.delete_note("n1", USER_A)
    store = FakeChroma.instances[0]
    assert store.calls[0][0] == "delete"
    where = store.calls[0][1]
    assert where["$and"][0]["note_id"] == "n1"
    assert where["$and"][1]["user_id"] == USER_A


def test_search_user_notes():
    idx = NoteVectorIndex()
    note_ids = idx.search_user_notes("q", USER_A, 5)
    assert note_ids == ["n1"]
    store = FakeChroma.instances[0]
    _, q, k, filt = store.calls[0]
    assert q == "q" and k == 5
    assert filt == _user_note_filter(USER_A)


def test_search_related_notes():
    idx = NoteVectorIndex()
    out = idx.search_related_notes("q", USER_A, 5)
    assert out[0][0].metadata["note_id"] == "n1"
    assert out[0][1] == 0.1


def test_find_related_excludes_self(monkeypatch):
    idx = NoteVectorIndex()
    idx._ensure_store()  # 先建实例，再替换其搜索方法

    def _search(query, k=None, filter=None):
        return [(NS(metadata={"note_id": "n1"}, page_content="a"), 0.1),
                (NS(metadata={"note_id": "n2"}, page_content="b"), 0.2)]
    monkeypatch.setattr(FakeChroma.instances[0], "similarity_search_with_score",
                        _search)
    out = idx.find_related_for_note_content("c", USER_A, "n1", 3)
    assert [d.metadata["note_id"] for d, _ in out] == ["n2"]


def test_search_self_heal_retry_on_stale(monkeypatch):
    idx = NoteVectorIndex()
    calls = {"n": 0}

    def _search(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("Error finding id")
        return ["recovered"]
    monkeypatch.setattr(idx, "_reset_store", lambda: None)
    out = idx._search_with_self_heal(_search)
    assert out == ["recovered"]
    assert calls["n"] == 2


def test_search_self_heal_raises_non_stale():
    idx = NoteVectorIndex()

    def _search(*a, **k):
        raise RuntimeError("普通错误")
    with pytest.raises(RuntimeError):
        idx._search_with_self_heal(_search)
