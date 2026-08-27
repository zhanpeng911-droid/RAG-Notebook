"""DocumentProcessor 单元测试 —— 加载分发、chunk 参数覆盖与入库状态机。

file_handler 在 conftest 下为 mock，这里在 processor 模块命名空间注入
可控桩；DocumentProcessor 用 __new__ 旁路构造器（其内部会建真切分器）。
"""
import pytest

from tests.helpers.unmock import LANGCHAIN_STACK, install_config_stub, restore_real

restore_real(*LANGCHAIN_STACK)
install_config_stub()

import app.rag.document_handler.processor as proc_mod  # noqa: E402
from app.rag.document_handler.processor import DocumentProcessor  # noqa: E402
from langchain_core.documents import Document  # noqa: E402


class FakeStore:
    def __init__(self):
        self.added = []

    def add_documents(self, docs):
        self.added.append(list(docs))


class FakeMD5:
    def __init__(self):
        self.existing = set()
        self.saved = []

    async def check_md5_hex(self, m, u=None):
        return m in self.existing

    async def save_md5_hex(self, m, filename=None, original_filename=None, u=None):
        self.saved.append((m, filename, original_filename, u))


class RecSpliter:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def split_documents(self, docs):
        self.calls.append(docs)
        return self.chunks if self.chunks else []


def make_proc(chunks=None):
    p = DocumentProcessor.__new__(DocumentProcessor)
    p.vectors_store = FakeStore()
    p.md5_store = FakeMD5()
    p.spliter = RecSpliter(chunks)
    return p


class UFile:
    """模拟 UploadFile：支持 bytes 文件名与异步读取。"""

    def __init__(self, name, content=b"hello"):
        self.filename = name
        self._content = content

    async def read(self):
        return self._content


@pytest.fixture()
def md5_source(monkeypatch):
    """可 await 的文件 MD5 桩：按队列顺序发放，或按路径映射。"""
    box = {"queue": [], "map": {}}

    async def fake_md5(path):
        if path in box["map"]:
            return box["map"][path]
        return box["queue"].pop(0) if box["queue"] else f"md5-{len(box['queue'])}-{path[-6:]}"

    monkeypatch.setattr(proc_mod, "get_file_md5_hex", fake_md5)
    return box


@pytest.fixture()
def dispatch(monkeypatch):
    """按扩展名分发各加载器桩，返回记录盒。"""
    box = {}
    async def mk(tag):
        async def loader(path):
            box.setdefault(tag, []).append(path)
            return box.get(f"{tag}:doc", [{"page_content": tag}])
        return loader
    def mks(tag):
        def loader(path):
            box.setdefault(tag, []).append(path)
            return [{"page_content": tag}]
        return loader

    async def txt_loader(path):
        box.setdefault("txt", []).append(path)
        return [{"page_content": "txt"}]

    async def markdown_loader(path):
        box.setdefault("markdown", []).append(path)
        return [{"page_content": "markdown"}]

    async def ppt_loader(path):
        box.setdefault("pptx", []).append(path)
        return [{"page_content": "pptx"}]

    async def word_loader(path):
        box.setdefault("word", []).append(path)
        return [{"page_content": "word"}]

    async def pdf_loader(path):
        box.setdefault("pdf", []).append(path)
        return [{"page_content": "pdf"}]

    def txt_loader_sync(path):
        box.setdefault("txt_sync", []).append(path)
        return [{"page_content": "txt"}]

    def markdown_loader_sync(path):
        box.setdefault("markdown_sync", []).append(path)
        return [{"page_content": "markdown"}]

    def ppt_loader_sync(path):
        box.setdefault("pptx_sync", []).append(path)
        return [{"page_content": "pptx"}]

    def word_loader_sync(path):
        box.setdefault("word_sync", []).append(path)
        return [{"page_content": "word"}]

    def pdf_loader_sync(path):
        box.setdefault("pdf_sync", []).append(path)
        return [{"page_content": "pdf"}]

    async def multi(path, md5v, uid):
        box.setdefault("multi", []).append((path, md5v, uid))
        return [{"page_content": "mm"}]

    def multi_sync(path, md5v, uid):
        box.setdefault("multi_sync", []).append((path, md5v, uid))
        return [{"page_content": "mm"}]

    # _LOADER_MAP 在导入期绑定了 conftest 的 mock 函数引用，
    # 必须整表替换才能让非 PDF 分发命中桩
    monkeypatch.setattr(proc_mod, "_LOADER_MAP", {
        ".txt": (txt_loader, txt_loader_sync),
        ".md": (markdown_loader, markdown_loader_sync),
        ".pptx": (ppt_loader, ppt_loader_sync),
        ".docx": (word_loader, word_loader_sync),
    })
    monkeypatch.setattr(proc_mod, "pdf_loader", pdf_loader)
    monkeypatch.setattr(proc_mod, "pdf_loader_sync", pdf_loader_sync)
    monkeypatch.setattr(proc_mod, "pdf_multimodal_loader", multi)
    monkeypatch.setattr(proc_mod, "pdf_multimodal_loader_sync", multi_sync)
    return box


def test_get_ext_lowercased():
    p = make_proc()
    assert p._get_ext("C:/X/FILE.PDF") == ".pdf"
    assert p._get_ext("noext") == ""


def test_resolve_chunk_params_defaults_without_override(monkeypatch):
    cfg = {
        "chunk_size": 500, "chunk_overlap": 60,
        "chunk_by_extension": {},
    }
    monkeypatch.setattr(proc_mod, "chroma_config", cfg)
    assert DocumentProcessor.resolve_chunk_params("a.txt") == (500, 60)


def test_resolve_chunk_params_per_extension_and_fallback(monkeypatch):
    cfg = {
        "chunk_size": 500, "chunk_overlap": 60,
        "chunk_by_extension": {"pdf": {"chunk_size": 800}},
    }
    monkeypatch.setattr(proc_mod, "chroma_config", cfg)
    # 扩展名有覆盖时只改 size、overlap 回退全局
    assert DocumentProcessor.resolve_chunk_params("x.PDF") == (800, 60)
    # 无扩展名 / 无覆盖均回退全局默认
    assert DocumentProcessor.resolve_chunk_params("zzz") == (500, 60)
    assert DocumentProcessor.resolve_chunk_params(None) == (500, 60)


@pytest.mark.asyncio
async def test_async_dispatch_unknown_ext_returns_empty(dispatch):
    p = make_proc()
    assert await p.get_file_document("/tmp/ghost.xyz") == []


@pytest.mark.asyncio
async def test_async_dispatch_map_formats(dispatch):
    p = make_proc()
    await p.get_file_document("/f/a.TXT")
    await p.get_file_document("/f/b.Md")
    await p.get_file_document("/f/c.pptx")
    await p.get_file_document("/f/d.docx")
    assert len(dispatch["txt"]) == 1
    assert len(dispatch["markdown"]) == 1
    assert len(dispatch["pptx"]) == 1
    assert len(dispatch["word"]) == 1


@pytest.mark.asyncio
async def test_pdf_multimodal_when_context_given(dispatch):
    p = make_proc()
    out = await p.get_file_document("/f/p.pdf", "M1", "u1")
    assert dispatch["multi"] == [("/f/p.pdf", "M1", "u1")]
    assert out == [{"page_content": "mm"}]


@pytest.mark.asyncio
async def test_pdf_multimodal_failure_degrades_to_plain(dispatch, monkeypatch):
    async def fail(path, md5v, uid):
        raise RuntimeError("视觉服务不可用")

    monkeypatch.setattr(proc_mod, "pdf_multimodal_loader", fail)
    p = make_proc()
    await p.get_file_document("/f/p.pdf", "M1", "u1")
    assert dispatch["pdf"] == ["/f/p.pdf"]


@pytest.mark.asyncio
async def test_pdf_without_full_context_uses_plain_loader(dispatch):
    p = make_proc()
    await p.get_file_document("/f/p.pdf")                    # 无 md5/user
    await p.get_file_document("/f/p.pdf", "only-md5")         # 缺 user
    assert dispatch["pdf"] == ["/f/p.pdf", "/f/p.pdf"]
    assert "multi" not in dispatch


def test_sync_dispatch_mirrors_async(dispatch):
    p = make_proc()
    p.get_file_document_sync("/s/a.txt")
    p.get_file_document_sync("/s/z.xyz")
    p.get_file_document_sync("/s/p.pdf", "M", "u")
    assert dispatch["txt_sync"] == ["/s/a.txt"]
    assert dispatch["multi_sync"] == [("/s/p.pdf", "M", "u")]
    assert p.get_file_document_sync("/s/z.xyz") == []


@pytest.mark.asyncio
async def test_ingest_happy_path_stamps_metadata_and_cleans_tmp(md5_source):
    proc = make_proc(chunks=[Document(page_content="正文", metadata={})])
    f = UFile("上传 记录.txt", b"data")
    steps = []

    async def cb(ev):
        steps.append(ev["step"])

    got_doc = [Document(page_content="正文", metadata={})]

    async def fake_load(path, md5=None, user_id=None):
        return got_doc

    proc.get_file_document = fake_load
    await proc.get_document(files=[f], user_id="u1",
                            progress_callback=cb, space_id="SP1")

    doc_out = proc.vectors_store.added[0][0]
    assert doc_out.metadata["user_id"] == "u1"
    assert doc_out.metadata["original_filename"] == "上传 记录.txt"
    assert doc_out.metadata["space_id"] == "SP1"
    assert proc.md5_store.saved and proc.md5_store.saved[0][3] == "u1"
    assert steps == ["loading", "splitting", "storing", "completed"]


@pytest.mark.asyncio
async def test_ingest_bytes_filename_decoded(md5_source):
    proc = make_proc(chunks=[Document(page_content="c", metadata={})])
    f = UFile(b"byte-name.txt")

    async def fake_load(path, md5=None, user_id=None):
        return [Document(page_content="c", metadata={})]

    proc.get_file_document = fake_load
    await proc.get_document(files=[f], user_id="u9")
    meta = proc.vectors_store.added[0][0].metadata
    assert meta["original_filename"] == b"byte-name.txt"


@pytest.mark.asyncio
async def test_ingest_skips_duplicated_md5(md5_source):
    proc = make_proc(chunks=[{"never": True}])
    md5_source["queue"] = ["will-match"]
    proc.md5_store.existing.add("will-match")
    seen = []

    async def cb(ev):
        seen.append(ev["step"])

    async def fake_load(*a, **k):
        raise AssertionError("重复文件不应触发加载")

    proc.get_file_document = fake_load
    await proc.get_document(files=[UFile("dup.txt")], user_id="u1",
                            progress_callback=cb)
    assert seen == ["skipping"]
    assert proc.vectors_store.added == []


@pytest.mark.asyncio
async def test_ingest_empty_loaded_content_is_error_step(md5_source):
    proc = make_proc()

    async def fake_load(path, md5=None, user_id=None):
        return []

    events = []

    async def cb(ev):
        events.append(ev)

    proc.get_file_document = fake_load
    await proc.get_document(files=[UFile("empty.txt")], user_id="u1",
                            progress_callback=cb)
    errs = [e for e in events if e["step"] == "error"]
    assert errs and errs[0]["error_message"] == "文件内容为空"


@pytest.mark.asyncio
async def test_ingest_empty_split_result_is_error_step(md5_source):
    proc = make_proc(chunks=[])

    async def fake_load(path, md5=None, user_id=None):
        return [Document(page_content="非空", metadata={})]

    events = []

    async def cb(ev):
        events.append(ev)

    proc.get_file_document = fake_load
    await proc.get_document(files=[UFile("thin.txt")], user_id="u1",
                            progress_callback=cb)
    errs = [e for e in events if e["step"] == "error"]
    assert errs and errs[-1]["error_message"] == "文档切分后为空"


@pytest.mark.asyncio
async def test_ingest_loader_exception_reports_and_continues(tmp_path, md5_source):
    proc = make_proc(chunks=[Document(page_content="可入库", metadata={})])
    calls = {"n": 0}

    async def fake_load(path, md5=None, user_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("磁盘错误")
        return [Document(page_content="OK", metadata={})]

    proc.get_file_document = fake_load
    events = []

    async def cb(ev):
        events.append((ev["step"], ev.get("error_message")))

    await proc.get_document(
        files=[UFile("bad.txt"), UFile("good.txt")], user_id="u1",
        progress_callback=cb,
    )
    assert ("error", "磁盘错误") in events
    assert proc.vectors_store.added and proc.md5_store.saved


@pytest.mark.asyncio
async def test_ingest_from_data_directory_when_files_none(monkeypatch, tmp_path, md5_source):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    src = data_dir / "seed.txt"
    src.write_text("种子内容", encoding="utf-8")

    async def fake_listdir(path, types):
        return (str(src),)

    monkeypatch.setattr(proc_mod, "listdir_allowed_type", fake_listdir)
    proc = make_proc(chunks=[Document(page_content="目录文档", metadata={})])

    async def fake_load(path, md5=None, user_id=None):
        return [Document(page_content="种子", metadata={})]

    proc.get_file_document = fake_load
    await proc.get_document(files=None, user_id="u7")
    assert proc.vectors_store.added[0][0].metadata["original_filename"] == "seed.txt"
    # 目录模式不删除源文件
    assert src.exists()
