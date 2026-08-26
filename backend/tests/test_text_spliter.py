"""AsyncTextSplitter 单元测试 —— 分块边界、重叠窗口与语义合并。

conftest 把 langchain_text_splitters 与 app.utils.config 都 mock 掉，
此处先恢复真实分割器并注入带 separators 的配置桩再导入目标模块。
"""
from tests.helpers.unmock import LANGCHAIN_STACK, install_config_stub, restore_real

restore_real(*LANGCHAIN_STACK)
install_config_stub()

from langchain_core.documents import Document  # noqa: E402

from app.rag.text_spliter import AsyncTextSplitter  # noqa: E402


class KeywordEmbedding:
    """确定性假嵌入：含 A 多返回 [1,0]，含 B 多返回 [0,1]。"""

    def embed_query(self, text):
        if text.count("A") >= text.count("B"):
            return [1.0, 0.0]
        return [0.0, 1.0]


def _sample_text():
    return "\n".join(["A" * 20, "A" * 20, "B" * 20, "B" * 20])


def test_short_text_returns_single_chunk():
    splitter = AsyncTextSplitter(chunk_size=100, chunk_overlap=10)
    import asyncio
    chunks = asyncio.run(splitter.split_text("短文本内容"))
    assert len(chunks) == 1
    assert chunks[0] == "短文本内容"


def test_long_text_chunks_respect_chunk_size():
    text = "\n".join(f"第{i}段" + "内容" * 15 for i in range(8))
    splitter = AsyncTextSplitter(chunk_size=40, chunk_overlap=5)
    import asyncio
    chunks = asyncio.run(splitter.split_text(text))
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 40


def test_no_content_lost_after_split():
    paragraphs = [f"P{i}-" + "字" * 30 for i in range(6)]
    text = "\n\n".join(paragraphs)
    splitter = AsyncTextSplitter(chunk_size=50, chunk_overlap=5)
    import asyncio
    chunks = asyncio.run(splitter.split_text(text))
    joined = "".join(chunks)
    for p in paragraphs:
        assert p[:4] in joined


def test_empty_text_tolerated():
    splitter = AsyncTextSplitter(chunk_size=50, chunk_overlap=5)
    import asyncio
    result = asyncio.run(splitter.split_text(""))
    assert isinstance(result, list)
    assert len(result) <= 1
    assert all(isinstance(c, str) for c in result)


def test_semantic_merge_combines_similar_chunks():
    # 4 个原始块：AA / AB / BB，同向量块应合并为 2 块
    splitter = AsyncTextSplitter(
        chunk_size=25, chunk_overlap=0,
        embedding_model=KeywordEmbedding(),
    )
    import asyncio
    chunks = asyncio.run(splitter.split_text(_sample_text()))
    assert len(chunks) == 2
    assert all("A" in c for c in chunks[0].split()) or chunks[0].startswith("A")
    assert "B" * 10 in chunks[1]


def test_without_embedding_model_no_merge_happens():
    splitter = AsyncTextSplitter(chunk_size=25, chunk_overlap=0)
    import asyncio
    chunks = asyncio.run(splitter.split_text(_sample_text()))
    assert len(chunks) == 4


def test_split_documents_keeps_metadata():
    docs = [
        Document(page_content="甲" * 60, metadata={"user_id": "u1"}),
        Document(page_content="乙" * 60, metadata={"user_id": "u2"}),
    ]
    splitter = AsyncTextSplitter(chunk_size=30, chunk_overlap=0)
    out = splitter.split_documents_sync(docs)
    assert len(out) >= 2
    metas = {d.metadata.get("user_id") for d in out}
    assert {"u1", "u2"} <= metas


def test_split_text_sync_matches_async_result():
    text = "\n".join("内容" * 12 + str(i) for i in range(5))
    sync_splitter = AsyncTextSplitter(chunk_size=45, chunk_overlap=5)
    async_splitter = AsyncTextSplitter(chunk_size=45, chunk_overlap=5)
    import asyncio
    assert (
        sync_splitter.split_text_sync(text)
        == asyncio.run(async_splitter.split_text(text))
    )


def test_cosine_similarity_cases():
    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=0)
    assert splitter._cosine_similarity([1, 0], [1, 0]) == 1.0
    assert splitter._cosine_similarity([1, 0], [0, 1]) == 0.0
    # 零向量安全返回 0，不抛除零错误
    assert splitter._cosine_similarity([0, 0], [1, 0]) == 0.0


def test_similarity_without_embedding_model_is_zero():
    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=0)
    assert splitter._calculate_similarity_sync("a", "b") == 0.0


def test_split_documents_async_path():
    import asyncio

    docs = [Document(page_content="甲" * 60, metadata={"k": "v"})]
    splitter = AsyncTextSplitter(chunk_size=30, chunk_overlap=0)
    out = asyncio.run(splitter.split_documents(docs))
    assert len(out) >= 1
    assert all(d.metadata.get("k") == "v" for d in out)


def test_split_text_sync_applies_semantic_merge():
    splitter = AsyncTextSplitter(
        chunk_size=25, chunk_overlap=0,
        embedding_model=KeywordEmbedding(),
    )
    chunks = splitter.split_text_sync(_sample_text())
    assert len(chunks) == 2


def test_optimize_paths_tolerate_empty_chunk_list():
    import asyncio

    splitter = AsyncTextSplitter(chunk_size=25, chunk_overlap=0)
    assert splitter._optimize_chunks_sync([]) == []
    assert asyncio.run(splitter._optimize_chunks([])) == []


def test_async_similarity_without_embedding_is_zero():
    import asyncio

    splitter = AsyncTextSplitter(chunk_size=10, chunk_overlap=0)
    assert asyncio.run(splitter._calculate_similarity("a", "b")) == 0.0
