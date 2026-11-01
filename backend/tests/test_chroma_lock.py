"""Cross-process Chroma write helper: batching and lock initialization."""
import pytest

from app.rag import chroma_lock


class FakeStore:
    def __init__(self):
        self.calls = []

    def add_documents(self, documents, ids=None):
        self.calls.append((list(documents), list(ids) if ids is not None else None))


@pytest.fixture(autouse=True)
def isolated_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(chroma_lock, "_lock", None)
    monkeypatch.setattr(chroma_lock, "_lock_path", lambda: str(tmp_path / "chroma.lock"))


def test_large_add_is_split_below_the_collection_limit():
    store = FakeStore()
    documents = [object() for _ in range(5)]
    ids = [f"id-{index}" for index in range(5)]
    chroma_lock.add_documents_batched(store, documents, ids=ids, batch_size=2)
    assert [len(batch[0]) for batch in store.calls] == [2, 2, 1]
    assert [len(batch[1]) for batch in store.calls] == [2, 2, 1]
    assert [item for batch in store.calls for item in batch[1]] == ids


def test_batch_size_above_collection_limit_is_rejected():
    with pytest.raises(ValueError):
        chroma_lock.add_documents_batched(
            FakeStore(), [object()], batch_size=chroma_lock.MAX_BATCH_SIZE + 1)


def test_ids_must_match_documents():
    with pytest.raises(ValueError):
        chroma_lock.add_documents_batched(FakeStore(), [object()], ids=[])


class FakeCollection:
    name = "synthetic"

    def __init__(self):
        self.upserts = []
        self.deletes = []
        self.queries = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def delete(self, **kwargs):
        self.deletes.append(kwargs)

    def query(self, **kwargs):
        self.queries.append(kwargs)


def test_native_reads_and_writes_are_serialized_across_processes():
    from types import SimpleNamespace

    collection = FakeCollection()
    store = SimpleNamespace(_chroma_collection=collection)
    chroma_lock.lock_collection_access(store)
    wrapped = store._chroma_collection
    assert isinstance(wrapped, chroma_lock.LockedCollection)
    assert wrapped.name == "synthetic"

    wrapped.upsert(ids=["a"], embeddings=[[0.0]], documents=["x"])
    wrapped.delete(ids=["a"])
    wrapped.query(query_embeddings=[[0.0]], n_results=1)
    assert collection.upserts and collection.deletes and collection.queries
    # Wrapping is idempotent across repeated initialization.
    chroma_lock.lock_collection_access(store)
    assert store._chroma_collection is wrapped


def test_server_mode_uses_no_file_lock(monkeypatch):
    monkeypatch.setenv("CHROMA_SERVER_URL", "http://127.0.0.1:8001")
    assert chroma_lock.server_mode() is True
    with chroma_lock.write_lock():
        pass
