"""VectorStoreService 单元测试 —— CRUD 聚合、删除级联与降级隔离。

单例构造会读真实配置并创建 Chroma 实例，这里统一用 __new__ 旁路，
注入记录调用的假向量库/假子服务；图片清理函数在模块命名空间打桩。
"""
from unittest.mock import MagicMock

import pytest

from tests.helpers.unmock import RETRIEVAL_STACK, restore_real

restore_real(*RETRIEVAL_STACK)
# conftest 预注册的 app.rag.vector_store 假条目会短路父包绑定，
# 先删假条目并显式加载中间包，再导入真实实现
restore_real("app.rag.vector_store")

import app  # noqa: E402,F401
import app.rag  # noqa: E402,F401
import app.rag.vector_store as vs_mod  # noqa: E402
from app.rag.vector_store import (  # noqa: E402
    VectorStoreService,
    reset_chroma_db_explicit,
)


class FakeVectors:
    """假向量库：记录 get/delete 调用，返回预置的 Chroma 风格结果。"""

    def __init__(self, result=None, get_error=None):
        self.result = result or {"ids": [], "documents": [], "metadatas": []}
        self.get_calls = []
        self.delete_calls = []
        self.get_error = get_error

    def get(self, include=None, where=None):
        if self.get_error:
            raise self.get_error
        self.get_calls.append({"include": include, "where": where})
        return dict(self.result)

    def delete(self, where=None, ids=None):
        self.delete_calls.append({"where": where, "ids": ids})


class _AwaitableMock(MagicMock):
    """既可当普通返回值，也可被 await（结果为 None）。"""

    def __await__(self):
        return iter(())


class Rec:
    """通用记录桩：任何方法调用都写入 calls，返回值均可 await。"""

    def __init__(self, returns=None):
        self.calls = []
        self._returns = returns or {}

    def __getattr__(self, name):
        def handler(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in self._returns:
                return self._returns[name]()
            return _AwaitableMock()
        return handler


class FakeMD5:
    def __init__(self):
        self.checks = []
        self.saved = []
        self.saved_sync = []
        self.by_filename = {}
        self.by_md5_ok = False
        self.deleted_users = []
        self.info_return = None
        self.all_rows = []

    async def check_md5_hex(self, m, u=None):
        self.checks.append((m, u))
        return m in self.by_filename.values()

    async def save_md5_hex(self, m, filename=None, original_filename=None, u=None):
        self.saved.append((m, filename, original_filename, u))

    def save_md5_hex_sync(self, m, filename=None, original_filename=None, u=None):
        self.saved_sync.append((m, filename, original_filename, u))

    async def delete_by_filename(self, u, f):
        got = self.by_filename.get(f)
        self.by_filename[f] = None
        return got

    async def delete_single_md5(self, u, m):
        return self.by_md5_ok

    async def delete_user_md5(self, u):
        self.deleted_users.append(u)

    async def get_md5_info(self, u, m):
        return self.info_return

    async def get_all_md5_records(self, u):
        return self.all_rows


def make_service(store=None):
    svc = VectorStoreService.__new__(VectorStoreService)
    svc.vectors_store = store or FakeVectors()
    svc.md5_store = FakeMD5()
    svc.hybrid_retriever = Rec()
    svc.document_processor = Rec()
    return svc


@pytest.fixture()
def image_calls(monkeypatch):
    box = {"dirs": [], "users": []}
    monkeypatch.setattr(
        vs_mod, "delete_image_directory",
        lambda u, m: box["dirs"].append((u, m)),
    )
    monkeypatch.setattr(
        vs_mod, "delete_user_all_images",
        lambda u: box["users"].append(u),
    )
    return box


# ---------- 初始化与显式重置 ----------

def test_singleton_double_checked_lock(tmp_path, monkeypatch):
    # 单例状态是类级的，测试后必须复位避免污染其他用例
    monkeypatch.setattr(VectorStoreService, "_instance", None)
    monkeypatch.setattr(VectorStoreService, "_initialized", False)
    # 直接放置实例而非触发真实初始化，只验证双重检查语义
    sentinel = object()
    VectorStoreService._instance = sentinel
    assert VectorStoreService() is sentinel
    monkeypatch.undo()


def test_reset_chroma_db_explicit_removes_dir(tmp_path):
    victim = tmp_path / "chroma-data"
    victim.mkdir()
    (victim / "x.bin").write_bytes(b"1")
    reset_chroma_db_explicit(str(victim))
    assert not victim.exists()


def test_reset_chroma_db_explicit_tolerates_missing(tmp_path):
    reset_chroma_db_explicit(str(tmp_path / "not-there"))


# ---------- 透传层 ----------

@pytest.mark.asyncio
async def test_passthroughs_forward_arguments():
    svc = make_service()
    await svc.get_bm25_retriever("u1")
    assert svc.hybrid_retriever.calls[0][0] == "get_bm25_retriever"
    assert svc.hybrid_retriever.calls[0][2] == {"user_id": "u1"} or \
        svc.hybrid_retriever.calls[0][1] == ("u1",)

    await svc.get_retriever("q", "u1", space_id="s1", candidate_k=9)
    assert svc.hybrid_retriever.calls[-1][0] == "get_retriever"

    w = await VectorStoreService.get_dynamic_weights(None)
    assert w == [0.5, 0.5]

    svc.get_file_document_sync("/tmp/f.pdf", "m")
    svc.split_documents_sync([])
    await svc.get_document(files=["f"], user_id="u1", progress_callback=None,
                           space_id="s")
    proc_names = [c[0] for c in svc.document_processor.calls]
    assert "get_file_document_sync" in proc_names
    assert "split_documents_sync" in proc_names
    assert "get_document" in proc_names


@pytest.mark.asyncio
async def test_md5_passthroughs():
    svc = make_service()
    svc.md5_store.by_filename = {"a.txt": "m-a"}
    assert await svc.check_md5_hex("m-a", "u1") is True
    await svc.save_md5_hex("m-new", "n.txt", "新名.txt", "u1")
    assert svc.md5_store.saved[-1][:2] == ("m-new", "n.txt")
    svc.save_md5_hex_sync("m-sync-sync")
    assert svc.md5_store.saved_sync[-1][0] == "m-sync-sync"
    svc.md5_store.info_return = {"md5": "m-i"}
    assert await svc.get_md5_info("u1", "m-i") == {"md5": "m-i"}
    svc.md5_store.all_rows = [{"md5": "z"}]
    assert await svc.get_all_md5_records("u1") == [{"md5": "z"}]


@pytest.mark.asyncio
async def test_md5_getters_swallow_errors_to_safe_defaults():
    svc = make_service()
    async def boom(*a, **k):
        raise RuntimeError("disk gone")
    svc.md5_store.get_md5_info = boom
    svc.md5_store.get_all_md5_records = boom
    assert await svc.get_md5_info("u1", "m") is None
    assert await svc.get_all_md5_records("u1") == []


# ---------- 删除级联 ----------

@pytest.mark.asyncio
async def test_delete_by_filename_found_cascades(image_calls):
    store = FakeVectors()
    svc = make_service(store)
    svc.md5_store.by_filename = {"旧文件.pdf": "m-del"}
    ok = await svc.delete_by_filename("u1", "旧文件.pdf")
    assert ok is True
    assert store.delete_calls[0]["where"] == {
        "$and": [{"user_id": "u1"}, {"md5": "m-del"}],
    }
    assert image_calls["dirs"] == [("u1", "m-del")]


@pytest.mark.asyncio
async def test_delete_by_filename_missing_returns_false(image_calls):
    svc = make_service(FakeVectors())
    assert await svc.delete_by_filename("u1", "ghost.pdf") is False
    assert image_calls["dirs"] == []


@pytest.mark.asyncio
async def test_delete_by_filename_skips_vectors_when_disabled(image_calls):
    store = FakeVectors()
    svc = make_service(store)
    svc.md5_store.by_filename = {"keep-docs.pdf": "m-k"}
    ok = await svc.delete_by_filename("u1", "keep-docs.pdf",
                                      delete_documents=False)
    assert ok is True
    assert store.delete_calls == []
    assert image_calls["dirs"] == [("u1", "m-k")]


@pytest.mark.asyncio
async def test_delete_by_filename_exception_returns_false(monkeypatch):
    svc = make_service(FakeVectors())
    async def boom(u, f):
        raise RuntimeError("io error")
    svc.md5_store.delete_by_filename = boom
    assert await svc.delete_by_filename("u1", "x.pdf") is False


@pytest.mark.asyncio
async def test_delete_single_md5_paths(image_calls):
    store = FakeVectors()
    svc = make_service(store)
    assert await svc.delete_single_md5("u1", "gone-md5") is False

    svc.md5_store.by_md5_ok = True
    assert await svc.delete_single_md5("u1", "ok-md5") is True
    assert store.delete_calls[0]["where"] == {
        "$and": [{"user_id": "u1"}, {"md5": "ok-md5"}],
    }
    assert image_calls["dirs"][-1] == ("u1", "ok-md5")


@pytest.mark.asyncio
async def test_delete_user_md5_with_and_without_documents(image_calls):
    store = FakeVectors()
    svc = make_service(store)
    await svc.delete_user_md5("u1", delete_documents=True)
    assert store.delete_calls[0]["where"] == {"user_id": "u1"}
    assert svc.md5_store.deleted_users == ["u1"]
    assert image_calls["users"] == ["u1"]

    store.delete_calls.clear()
    await svc.delete_user_md5("u2", delete_documents=False)
    assert store.delete_calls == []
    assert svc.md5_store.deleted_users == ["u1", "u2"]


@pytest.mark.asyncio
async def test_delete_user_documents_reraises(image_calls):
    svc = make_service(FakeVectors())
    async def broken(u, *args, **kwargs):
        raise RuntimeError("boom")
    svc.delete_user_md5 = broken
    with pytest.raises(RuntimeError):
        await svc.delete_user_documents("u1")


# ---------- 文档聚合与详情 ----------

def _result(ids, docs, metas):
    return {"ids": ids, "documents": docs, "metadatas": metas}


@pytest.mark.asyncio
async def test_get_user_documents_aggregates_by_original_filename():
    long_content = "内容A" * 40
    res = _result(
        ["id1", "id2", "id3"],
        [long_content, "内容B" * 40, "内容C" * 40],
        [
            {"original_filename": "报告.pdf", "source": "/tmp/x1.pdf",
             "user_id": "u1", "space_id": "s1", "created_at": "t1",
             "image_paths": ["i1.png", "i2.png"]},
            {"original_filename": "报告.pdf", "space_id": "s1"},
            {"original_filename": "总结.docx", "source": r"C:\tmp\y.docx",
             "user_id": "u1", "space_id": "s1", "image_paths": "坏类型"},
        ],
    )
    store = FakeVectors(result=res)
    svc = make_service(store)

    out = await svc.get_user_documents(user_id="u1", space_id="s1")

    # 服务端下推 $and 过滤
    assert store.get_calls[0]["where"] == {
        "$and": [{"user_id": "u1"}, {"space_id": "s1"}],
    }
    by_name = {d["filename"]: d for d in out}
    assert set(by_name) == {"报告.pdf", "总结.docx"}
    rep = by_name["报告.pdf"]
    assert rep["chunk_count"] == 2
    assert rep["image_count"] == 2
    assert len(rep["preview"]) < 110 and rep["preview"].endswith("...")
    assert by_name["总结.docx"]["image_count"] == 0
    assert by_name["总结.docx"]["filename"] == "总结.docx"


@pytest.mark.asyncio
async def test_get_user_documents_client_side_space_filter():
    res = _result(
        ["id1", "id2"],
        ["甲", "乙"],
        [
            {"original_filename": "a.pdf", "space_id": "s1"},
            {"original_filename": "b.pdf", "space_id": "s2"},
        ],
    )
    svc = make_service(FakeVectors(result=res))
    out = await svc.get_user_documents(user_id="u1", space_id="s1")
    assert [d["filename"] for d in out] == ["a.pdf"]


@pytest.mark.asyncio
async def test_get_user_documents_where_clause_variants():
    svc = make_service(FakeVectors())
    await svc.get_user_documents(user_id="u1")
    assert svc.vectors_store.get_calls[-1]["where"] == {"user_id": "u1"}
    await svc.get_user_documents(space_id="s1")
    assert svc.vectors_store.get_calls[-1]["where"] == {"space_id": "s1"}
    await svc.get_user_documents()
    assert svc.vectors_store.get_calls[-1]["where"] is None


@pytest.mark.asyncio
async def test_get_user_documents_propagates_errors():
    svc = make_service(FakeVectors(get_error=RuntimeError("down")))
    with pytest.raises(RuntimeError):
        await svc.get_user_documents(user_id="u1")


@pytest.mark.asyncio
async def test_get_document_detail_joins_matches_only():
    res = _result(
        ["c1", "c2", "c3"],
        ["第一段", "第二段", "别人的"],
        [
            {"source": "/up/a.pdf", "original_filename": "论文.pdf",
             "md5": "M1", "user_id": "u1", "page": 1,
             "image_paths": ["p1.png"]},
            {"original_filename": "论文.pdf", "md5": "M1", "page": 2},
            {"original_filename": "别的.pdf", "md5": "OTHER"},
        ],
    )
    svc = make_service(FakeVectors(result=res))
    info = await svc.get_document_detail("u1", "论文.pdf")
    assert info["chunk_count"] == 2
    assert info["content"] == "第一段\n第二段"
    assert info["images"] == ["/knowledge/image/M1/p1.png"]
    assert [c["index"] for c in info["chunks"]] == [0, 1]
    assert info["chunks"][0]["images"] == ["/knowledge/image/M1/p1.png"]
    assert info["chunks"][1]["images"] == []
    assert info["md5"] == "M1"


@pytest.mark.asyncio
async def test_get_document_detail_no_match_returns_none():
    res = _result(["c1"], ["x"], [{"original_filename": "zzz.pdf"}])
    svc = make_service(FakeVectors(result=res))
    assert await svc.get_document_detail("u1", "none.pdf") is None


@pytest.mark.asyncio
async def test_get_document_chunks_matching_ladder():
    res = _result(
        ["k1", "k2", "k3", "k4"],
        ["精确", "无扩展名", "子串", "全值扫描"],
        [
            {"source": "/x/report.pdf", "md5": "M"},
            {"source": "report", "md5": "M"},
            {"source": "/deep/nested/report-final.pdf", "md5": "M"},
            {"note": "有关 report.pdf 的备注", "md5": "M"},
        ],
    )
    svc = make_service(FakeVectors(result=res))
    out = await svc.get_document_chunks("u1", "report.pdf")
    assert out["total_chunks"] == 4
    assert [c["index"] for c in out["chunks"]] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_get_document_chunks_zero_match_warns_but_returns():
    res = _result(["k1"], ["x"], [{"original_filename": "aaa.pdf", "md5": "M"}])
    svc = make_service(FakeVectors(result=res))
    out = await svc.get_document_chunks("u1", "bbb.pdf")
    assert out["total_chunks"] == 0
    assert out["chunks"] == []


@pytest.mark.asyncio
async def test_get_doc_entry_boundary_defaults():
    meta, content = VectorStoreService._get_doc_entry(
        {"metadatas": [], "documents": ["only-text"]}, 0,
    )
    assert meta == {} and content == "only-text"
