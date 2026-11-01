"""Offline failure proofs: embedding failures differ from uncertain mutations."""

from types import SimpleNamespace

import httpx
import pytest

from app.rag.vector_io import (
    EMBED_BATCH_SIZE,
    ObservedCollection,
    ObservedEmbeddings,
    VectorBatchError,
    VectorDependencyError,
    exception_details,
    safe_endpoint,
)
from app.rag.chroma_lock import add_documents_batched


def test_endpoint_diagnostics_never_include_credentials_or_query():
    assert (
        safe_endpoint("https://secret:password@example.test:443/path?token=private")
        == "example.test:443"
    )
    details = exception_details(ValueError("private document text and token"))
    assert "private" not in str(details)
    assert details["exception_chain"][0]["type"] == "ValueError"


def test_embedding_batches_preserve_all_input_order():
    seen = []

    class Embed:
        def embed_documents(self, texts):
            seen.append(list(texts))
            return [[int(text)] for text in texts]

    count = EMBED_BATCH_SIZE * 2 + 1
    wrapped = ObservedEmbeddings(Embed(), "http://localhost:11434")
    assert wrapped.embed_documents([str(i) for i in range(count)]) == [[i] for i in range(count)]
    assert [len(batch) for batch in seen] == [EMBED_BATCH_SIZE, EMBED_BATCH_SIZE, 1]


def test_connect_failure_is_retried_once_but_read_timeout_is_not():
    attempts = []

    class Embed:
        def embed_query(self, text):
            attempts.append(text)
            if len(attempts) == 1:
                raise httpx.ConnectTimeout("contains private input")
            return [1.0]

    assert ObservedEmbeddings(Embed(), "http://localhost:11434").embed_query("q") == [1.0]
    assert len(attempts) == 2
    attempts.clear()

    class ReadFailure:
        def embed_query(self, text):
            attempts.append(text)
            raise httpx.ReadTimeout("private input")

    with pytest.raises(VectorDependencyError) as error:
        ObservedEmbeddings(ReadFailure(), "http://localhost:11434").embed_query("q")
    assert len(attempts) == 1
    assert not error.value.write_uncertain
    assert "private" not in str(error.value.details)


def test_embedding_failure_after_one_acknowledged_batch_proves_writer_stopped():
    class Embed:
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise httpx.ReadTimeout("embedding interrupted")
            return [[1.0] for _ in texts]

    embedding = ObservedEmbeddings(Embed(), "http://localhost:11434")
    written = []

    class Store:
        def add_documents(self, docs, ids=None):
            embedding.embed_documents([doc.page_content for doc in docs])
            written.extend(ids)

    with pytest.raises(VectorBatchError) as failure:
        add_documents_batched(
            Store(),
            [SimpleNamespace(page_content="x") for _ in range(2)],
            ids=["first", "second"],
            batch_size=1,
        )
    assert written == ["first"]
    assert failure.value.writer_stopped is True
    assert failure.value.details["confirmed_batches"] == 1
    assert failure.value.details["operation"] == "embedding.documents"


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.WriteTimeout, OSError])
def test_mutation_timeout_is_neither_retried_nor_mistaken_for_a_stopped_writer(error_type):
    class Collection:
        calls = 0

        def upsert(self, **kwargs):
            self.calls += 1
            raise error_type("WinError 10060; server might still be writing")

    native = Collection()
    collection = ObservedCollection(native, "http://localhost:8001")

    class Store:
        def add_documents(self, docs, ids=None):
            collection.upsert(ids=ids)

    with pytest.raises(VectorBatchError) as failure:
        add_documents_batched(Store(), [object()], ids=["one"])
    assert native.calls == 1
    assert failure.value.writer_stopped is False
    assert failure.value.details["write_uncertain"] is True
    assert failure.value.details["endpoint"] == "localhost:8001"


def test_chroma_preconnect_failure_does_not_leave_an_unprovable_writer():
    class Collection:
        calls = 0

        def upsert(self, **kwargs):
            self.calls += 1
            raise httpx.ConnectTimeout("request could not connect")

    native = Collection()
    collection = ObservedCollection(native, "http://localhost:8001")

    class Store:
        def add_documents(self, docs, ids=None):
            collection.upsert(ids=ids)

    with pytest.raises(VectorBatchError) as failure:
        add_documents_batched(Store(), [object()], ids=["one"])
    assert native.calls == 1  # Do not retry native writes in the adapter.
    assert failure.value.writer_stopped is True
    assert failure.value.details["write_uncertain"] is False
