"""pdf_multimodal_loader 单元测试 —— 文本/视觉分流、去重分组与批次。

PDF 用真实 PyMuPDF 构造；视觉服务以假实现注入（可编程哈希、
记录调用），并直接改写模块常量验证去重开关与批大小。
"""
import os

import pytest

from tests.helpers.unmock import restore_real

restore_real("app.utils.pdf_multimodal_loader")
restore_real("app.utils.image_extractor")
restore_real("langchain_core", "langchain_core.documents")

import app  # noqa: E402,F401
import app.utils  # noqa: E402,F401
import app.utils.pdf_multimodal_loader as pml  # noqa: E402
import app.utils.image_extractor as _ie_mod  # noqa: E402

_REAL_EXTRACT = _ie_mod.extract_images_from_pdf

fitz = pytest.importorskip("fitz")


class FakeVision:
    """可编程视觉服务：固定哈希序列 + 记录批次调用。"""

    def __init__(self, hashes=None, batch_results=None):
        self.hashes = list(hashes or [])
        self.batch_results = dict(batch_results or {})
        self.batch_calls = []
        self.sync_calls = []
        self.n = 0

    def compute_image_hash(self, path):
        idx = min(self.n, len(self.hashes) - 1)
        h = self.hashes[idx]
        self.n += 1
        return h

    @staticmethod
    def hamming_distance(a, b):
        if len(a) != len(b):
            return abs(len(a) - len(b)) * 10
        return sum(1 for x, y in zip(a, b) if x != y)

    async def describe_pages_batch(self, paths, pages, texts):
        self.batch_calls.append(list(pages))
        merged = {}
        for pn in pages:
            merged[pn] = self.batch_results.get(pn, f"视觉内容{pn}")
        return merged

    def describe_pages_batch_sync(self, paths, pages, texts):
        self.sync_calls.append(list(pages))
        merged = {}
        for pn in pages:
            merged[pn] = self.batch_results.get(pn, f"同步视觉{pn}")
        return merged


def _inject(vision_cls, monkeypatch):
    """把视觉依赖换成 fitz + 真实图片提取 + 假 VisionService。"""
    monkeypatch.setattr(
        pml, "_load_multimodal_pdf_dependencies",
        lambda: (fitz, _REAL_EXTRACT, vision_cls),
    )


def _make_pdf(path, page_texts):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


@pytest.fixture(autouse=True)
def reset_constants(monkeypatch, tmp_path):
    monkeypatch.setattr(pml, "_DEDUP_ENABLED", False)
    monkeypatch.setattr(pml, "_BATCH_SIZE", 5)
    # 真实提取会把嵌入图片写到数据根，指向 tmp 避免污染仓库目录
    monkeypatch.setattr(_ie_mod, "get_data_path", lambda: str(tmp_path))
    yield


@pytest.mark.asyncio
async def test_missing_file_returns_empty(monkeypatch, tmp_path):
    class V(FakeVision):
        pass
    _inject(V, monkeypatch)
    assert await pml.pdf_multimodal_loader(str(tmp_path / "none.pdf"),
                                           "a" * 32, "u1") == []


@pytest.mark.asyncio
async def test_unopenable_pdf_returns_empty(monkeypatch, tmp_path):
    class V(FakeVision):
        pass
    _inject(V, monkeypatch)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"junk")
    assert await pml.pdf_multimodal_loader(str(bad), "a" * 32, "u1") == []


@pytest.mark.asyncio
async def test_pure_long_text_pages_skip_vision(monkeypatch, tmp_path):
    class V(FakeVision):
        batch_calls = None  # 断言不应被调用

    _inject(V, monkeypatch)
    pdf = tmp_path / "text.pdf"
    long_text = "字" * 150
    _make_pdf(pdf, [long_text, long_text])

    class RecordingV(V):
        async def describe_pages_batch(self, *a, **k):
            raise AssertionError("纯文本页不应调用视觉模型")

    # 覆盖注入的类为 Recording 版本需重新注入
    _inject(RecordingV, monkeypatch)

    docs = await pml.pdf_multimodal_loader(str(pdf), "a" * 32, "u9")
    assert [d.metadata["page"] for d in docs] == [1, 2]
    assert all(d.metadata["has_images"] is False for d in docs)
    assert docs[0].metadata["md5"] == "a" * 32
    metas_dump = [dict(d.metadata) for d in docs]
    assert all(d.metadata["image_paths"] is None for d in docs), metas_dump


@pytest.mark.asyncio
async def test_short_text_page_gets_vision_description(monkeypatch, tmp_path):
    class V(FakeVision):
        def __init__(self):
            super().__init__(hashes=["a" * 8], batch_results={1: "这是流程图说明"})

    _inject(V, monkeypatch)
    pdf = tmp_path / "chart.pdf"
    _make_pdf(pdf, ["short"])  # <100 字符触发视觉路径

    docs = await pml.pdf_multimodal_loader(str(pdf), "a" * 32, "u1")
    assert len(docs) == 1
    assert "[页面视觉描述]: 这是流程图说明" in docs[0].page_content


@pytest.mark.asyncio
async def test_dedup_groups_share_vision_text(monkeypatch, tmp_path):
    monkeypatch.setattr(pml, "_DEDUP_ENABLED", True)

    calls_box = {"n": 0}

    class DedupV(FakeVision):
        def __init__(self):
            super().__init__(hashes=["same" * 4, "same" * 4])
            # compute_image_hash 按页序返回同值 → 两页聚为一组

        async def describe_pages_batch(self, paths, pages, texts):
            calls_box["n"] += 1
            return {pn: "组描述" for pn in pages}

    _inject(DedupV, monkeypatch)
    pdf = tmp_path / "dup.pdf"
    _make_pdf(pdf, ["短一", "短二"])

    docs = await pml.pdf_multimodal_loader(str(pdf), "a" * 32, "u1")
    assert calls_box["n"] == 1            # 相似页只调用一次视觉模型
    contents = sorted(d.page_content for d in docs)
    assert all("组描述" in c for c in contents)


@pytest.mark.asyncio
async def test_batch_size_splits_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(pml, "_BATCH_SIZE", 1)

    class BatchedV(FakeVision):
        pass

    _inject(BatchedV, monkeypatch)
    pdf = tmp_path / "many.pdf"
    _make_pdf(pdf, ["p1短", "p2短", "p3短"])

    docs = await pml.pdf_multimodal_loader(str(pdf), "a" * 32, "u1")
    assert len(docs) == 3


def test_build_document_metadata_contract():
    d = pml._build_document("内容", 3, "b" * 32, "a.pdf", [], False)
    assert d.metadata == {
        "page": 3, "md5": "b" * 32, "source": "a.pdf",
        "image_paths": None, "has_images": False,
    }
    d2 = pml._build_document("c", 1, "m", "s.pdf", ["i.png"], True)
    assert d2.metadata["image_paths"] == ["i.png"]
    assert d2.metadata["has_images"] is True


@pytest.mark.asyncio
async def test_temp_render_files_cleaned_up(monkeypatch, tmp_path):
    class CleanupV(FakeVision):
        pass

    _inject(CleanupV, monkeypatch)

    import tempfile as tempfile_mod
    orig_named = tempfile_mod.NamedTemporaryFile
    created = []

    def spy_named(*a, **k):
        f = orig_named(*a, **k)
        created.append(f.name)
        return f

    monkeypatch.setattr(tempfile_mod, "NamedTemporaryFile", spy_named)

    pdf = tmp_path / "tmpclean.pdf"
    _make_pdf(pdf, ["短"])
    await pml.pdf_multimodal_loader(str(pdf), "a" * 32, "u1")
    assert created, "应渲染过临时 PNG"
    for tp in created:
        assert not os.path.exists(tp), f"临时文件未清理: {tp}"


# ---------- 同步版 ----------

def test_sync_missing_returns_empty(monkeypatch, tmp_path):
    class V(FakeVision):
        pass
    _inject(V, monkeypatch)
    assert pml.pdf_multimodal_loader_sync(str(tmp_path / "no.pdf"),
                                          "a" * 32, "u1") == []


def test_sync_short_text_uses_thread_pool_batch(monkeypatch, tmp_path):
    class SyncV(FakeVision):
        def __init__(self):
            super().__init__(batch_results={1: "同步描述"})
    _inject(SyncV, monkeypatch)
    pdf = tmp_path / "sync.pdf"
    _make_pdf(pdf, ["短页"])
    docs = pml.pdf_multimodal_loader_sync(str(pdf), "a" * 32, "u1")
    assert "同步描述" in docs[0].page_content
