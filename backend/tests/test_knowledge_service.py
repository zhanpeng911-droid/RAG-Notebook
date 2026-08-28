"""knowledge_service 直接单测 —— 不经 ASGI，规避 aiosqlite await 后 coverage 追踪缺陷。

覆盖：单/多文件上传校验、SSE 流程编排、切片线程函数、批量图片、
MD5 委托、文档详情/切片、进度计算。
"""
import io
from types import SimpleNamespace as NS

import pytest
from fastapi import UploadFile
from fastapi import HTTPException as FE

from app.core.exceptions import KnowledgeException

USER_A = "u-aaaa-0000-0000-000000000001"


def _upl(name="t.txt", content=b"hello"):
    return UploadFile(filename=name, file=io.BytesIO(content))


def async_fake(value):
    async def _f(*a, **k):
        return value
    return _f


def test_processing_state_progress():
    from app.router.knowledge_service import ProcessingState
    st = ProcessingState(total_files=2, total_valid=2)
    assert st.current_progress() == 0
    st.sliced_count = 1
    assert st.current_progress() == 30
    st.sliced_count = 2
    st.written_count = 2
    assert st.current_progress() == 99
    st2 = ProcessingState(total_files=0, total_valid=0)
    assert st2.current_progress() == 0


@pytest.mark.asyncio
async def test_single_ok(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    fake_store = NS(get_document=async_fake(None))
    monkeypatch.setattr("app.router.knowledge_service.VectorStoreService",
                        lambda: fake_store)
    out = await svc.handle_add_vector_single(_upl(), USER_A)
    assert out == "t.txt"


@pytest.mark.asyncio
async def test_single_too_large(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    monkeypatch.setattr("app.router.knowledge_service.validate_single_file_size",
                        lambda f: "超过 20MB")
    with pytest.raises(KnowledgeException):
        await svc.handle_add_vector_single(_upl(), USER_A)


@pytest.mark.asyncio
async def test_single_bad_type(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    monkeypatch.setattr("app.router.knowledge_service.validate_single_file_size",
                        lambda f: None)
    monkeypatch.setattr("app.router.knowledge_service.validate_file_type",
                        lambda c, n: "类型不支持")
    with pytest.raises(KnowledgeException) as ei:
        await svc.handle_add_vector_single(_upl(), USER_A)
    assert ei.value.code == 400


@pytest.mark.asyncio
async def test_multiple_ok(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    monkeypatch.setattr("app.router.knowledge_service.validate_total_size",
                        lambda n: None)

    async def _single(file, user_id, space_id=""):
        return file.filename
    monkeypatch.setattr(svc, "handle_add_vector_single", _single)

    out = await svc.handle_add_vector_multiple([_upl("a.txt"), _upl("b.txt")],
                                               USER_A)
    assert out == ["a.txt", "b.txt"]


@pytest.mark.asyncio
async def test_multiple_oversize(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    monkeypatch.setattr("app.router.knowledge_service.validate_total_size",
                        lambda n: "总大小超限")
    with pytest.raises(FE) as ei:
        await svc.handle_add_vector_multiple([_upl()], USER_A)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_multiple_reraises_on_failure(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    monkeypatch.setattr("app.router.knowledge_service.validate_total_size",
                        lambda n: None)

    async def _fail(file, user_id, space_id=""):
        raise RuntimeError("处理失败")
    monkeypatch.setattr(svc, "handle_add_vector_single", _fail)

    with pytest.raises(RuntimeError):
        await svc.handle_add_vector_multiple([_upl()], USER_A)


@pytest.mark.asyncio
async def test_validate_and_read_files(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()

    def _detect(content, filename):
        return "text/plain" if filename.endswith(".txt") else "application/octet-stream"

    monkeypatch.setattr("app.router.knowledge_service.detect_file_type", _detect)
    monkeypatch.setattr("app.router.knowledge_service.ALLOWED_MIME_TYPES",
                        {"text/plain"})
    monkeypatch.setattr("app.router.knowledge_service.ALLOWED_EXTENSIONS",
                        {".txt"})
    valid, errors, total = await svc._validate_and_read_files(
        [_upl("good.txt"), _upl("bad.exe")])
    assert total == 2
    assert len(valid) == 1
    assert valid[0]["filename"] == "good.txt"
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_validate_read_files_oversize(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    monkeypatch.setattr("app.router.knowledge_service.MAX_FOLDER_SIZE", 1)
    valid, errors, total = await svc._validate_and_read_files(
        [_upl(content=b"hello world")])
    assert total == 1
    assert valid == []
    assert len(errors) == 1


def _stub_slice_result(monkeypatch):
    """SliceResult 被 conftest mock 成 MagicMock，打桩成可断言的 NS。"""
    from app.rag import sse_models as sm
    monkeypatch.setattr(sm.SliceResult, "success_result",
                        lambda **k: NS(success=True))
    monkeypatch.setattr(sm.SliceResult, "error_result",
                        lambda **k: NS(success=False))


def test_sync_slice_file_success(monkeypatch):
    from app.router import knowledge_service as ks
    _stub_slice_result(monkeypatch)

    docs = [NS(metadata={})]
    fake_store = NS(
        get_file_document_sync=lambda path, md5, user_id: docs,
        split_documents_sync=lambda d: [NS(page_content="x", metadata={})],
    )
    monkeypatch.setattr(ks, "VectorStoreService", lambda: fake_store)
    monkeypatch.setattr(ks, "get_file_md5_hex_sync", lambda p: "abc123")

    puts = []

    class FakeQueue:
        def put(self, item):
            puts.append(item)

    ks._sync_slice_file(b"hello", "t.txt", 1, USER_A, FakeQueue())
    assert len(puts) == 1
    assert puts[0].success


def test_sync_slice_file_empty_docs(monkeypatch):
    from app.router import knowledge_service as ks
    _stub_slice_result(monkeypatch)

    fake_store = NS(get_file_document_sync=lambda p, md5, user_id: [],
                    split_documents_sync=lambda d: [])
    monkeypatch.setattr(ks, "VectorStoreService", lambda: fake_store)
    monkeypatch.setattr(ks, "get_file_md5_hex_sync", lambda p: "abc")

    puts = []

    class FakeQueue:
        def put(self, item):
            puts.append(item)

    ks._sync_slice_file(b"hello", "t.txt", 1, USER_A, FakeQueue())
    assert len(puts) == 1
    assert puts[0].success is False


def test_sync_slice_file_exception(monkeypatch):
    from app.router import knowledge_service as ks
    _stub_slice_result(monkeypatch)

    def _boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(ks, "VectorStoreService", _boom)
    monkeypatch.setattr(ks, "get_file_md5_hex_sync", lambda p: "abc")

    puts = []

    class FakeQueue:
        def put(self, item):
            puts.append(item)

    ks._sync_slice_file(b"hello", "t.txt", 1, USER_A, FakeQueue())
    assert len(puts) == 1
    assert puts[0].success is False


def test_start_slicing():
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    queue, executor, futures = svc._start_slicing(
        [{"content": b"x", "filename": "a.txt", "file_index": 1}], USER_A)
    assert queue is not None
    executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_process_slice_success_and_fail():
    from app.router.knowledge_service import KnowledgeService, ProcessingState
    svc = KnowledgeService()

    class FakeQueue:
        def __init__(self, items):
            self.items = list(items)

        def get(self, block=True, timeout=0.1):
            if not self.items:
                raise Exception("empty")
            return self.items.pop(0)

        def task_done(self):
            pass

    class FakeStore:
        vectors_store = NS(add_documents=lambda d: None)

        async def save_md5_hex(self, *a, **k):
            return None

    ok_res = NS(success=True, file_index=1, filename="ok.txt",
                documents=[NS(page_content="x")], md5="m1", chunk_count=1)
    fail_res = NS(success=False, file_index=2, filename="bad.txt", error="解析失败")
    queue = FakeQueue([ok_res, fail_res])
    state = ProcessingState(total_files=2, total_valid=2)

    events = [e async for e in svc._process_slice_results(
        queue, 2, FakeStore(), state, USER_A)]
    assert state.success_count == 1
    assert state.failed_count == 1
    assert state.slice_success_count == 1
    # 成功文件 3 事件 + 失败文件 1 事件
    assert len(events) == 4


@pytest.mark.asyncio
async def test_process_slice_write_error():
    from app.router.knowledge_service import KnowledgeService, ProcessingState
    svc = KnowledgeService()

    class FakeQueue:
        def __init__(self):
            self.exhausted = False

        def get(self, block=True, timeout=0.1):
            if self.exhausted:
                raise Exception("empty")
            self.exhausted = True
            return NS(success=True, file_index=1, filename="ok.txt",
                      documents=[NS(page_content="x")], md5="m1", chunk_count=1)

        def task_done(self):
            pass

    class BoomStore:
        vectors_store = NS(
            add_documents=lambda d: (_ for _ in ()).throw(RuntimeError("写入失败")))

        async def save_md5_hex(self, *a, **k):
            return None

    state = ProcessingState(total_files=1, total_valid=1)
    events = [e async for e in svc._process_slice_results(
        FakeQueue(), 1, BoomStore(), state, USER_A)]
    assert state.success_count == 0
    assert state.failed_count == 1
    assert state.written_count == 1
    assert len(events) == 3  # slicing_completed + writing + write_error


@pytest.mark.asyncio
async def test_stream_full_flow(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()

    async def fake_validate(files):
        return [{"content": b"x", "filename": "a.txt", "file_index": 1}], [], 1

    async def fake_process(queue, count, store, state, uid):
        yield "data: progress\n\n"

    def fake_start(valid_files, uid, space):
        return NS(), NS(shutdown=lambda wait=True: None), []

    monkeypatch.setattr(svc, "_validate_and_read_files", fake_validate)
    monkeypatch.setattr(svc, "_start_slicing", fake_start)
    monkeypatch.setattr(svc, "_process_slice_results", fake_process)

    events = [e async for e in svc.handle_add_vector_multiple_stream(
        [_upl()], USER_A)]
    assert len(events) == 3  # start + 进度 + finish


@pytest.mark.asyncio
async def test_stream_no_valid_files(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()

    async def fake_validate(files):
        return [], ["data: validation error\n\n"], 1

    monkeypatch.setattr(svc, "_validate_and_read_files", fake_validate)
    events = [e async for e in svc.handle_add_vector_multiple_stream(
        [_upl("bad.exe")], USER_A)]
    assert len(events) == 2  # start + validation error


@pytest.mark.asyncio
async def test_md5_delegation():
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    calls = {}

    class FakeRecord:
        async def clean_user_upload(self, user_id):
            calls["clean"] = True

        async def clear_user_md5(self, user_id, delete_documents):
            calls["clear"] = delete_documents

        async def delete_single_md5(self, user_id, md5, delete_documents):
            calls["del1"] = md5
            return True

        async def delete_by_filename(self, user_id, filename, delete_documents):
            calls["del2"] = filename
            return False

        async def get_md5_info(self, user_id, md5):
            calls["info"] = md5
            return {"md5": md5}

        async def get_all_md5_records(self, user_id):
            calls["all"] = True
            return []

    svc.record_service = FakeRecord()
    await svc.clean_user_upload("u")
    await svc.handle_clear_user_md5("u", False)
    assert await svc.handle_delete_single_md5("u", "m1", True) is True
    assert await svc.handle_delete_by_filename("u", "f.txt", True) is False
    assert (await svc.handle_get_md5_info("u", "m1"))["md5"] == "m1"
    assert await svc.handle_get_all_md5_records("u") == []
    assert calls == {"clean": True, "clear": False, "del1": "m1",
                     "del2": "f.txt", "info": "m1", "all": True}


@pytest.mark.asyncio
async def test_handle_get_user_knowledge(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    fake_store = NS(get_user_documents=async_fake([{"filename": "x.txt"}]))
    monkeypatch.setattr("app.router.knowledge_service.VectorStoreService",
                        lambda: fake_store)
    out = await svc.handle_get_user_knowledge(USER_A)
    assert out[0]["filename"] == "x.txt"


@pytest.mark.asyncio
async def test_handle_get_document_detail_missing(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    fake_store = NS(get_document_detail=async_fake(None))
    monkeypatch.setattr("app.router.knowledge_service.VectorStoreService",
                        lambda: fake_store)
    with pytest.raises(FE) as ei:
        await svc.handle_get_document_detail(USER_A, "x.txt")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_handle_document_chunks_empty(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    fake_store = NS(get_document_chunks=async_fake({"total_chunks": 0, "chunks": []}))
    monkeypatch.setattr("app.router.knowledge_service.VectorStoreService",
                        lambda: fake_store)
    out = await svc.handle_get_document_chunks(USER_A, "x.txt")
    assert out["total_chunks"] == 0


@pytest.mark.asyncio
async def test_handle_document_chunks_nonempty(monkeypatch):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    fake_store = NS(get_document_chunks=async_fake(
        {"total_chunks": 2, "chunks": [{"content": "a"}, {"content": "b"}]}))
    monkeypatch.setattr("app.router.knowledge_service.VectorStoreService",
                        lambda: fake_store)
    out = await svc.handle_get_document_chunks(USER_A, "x.txt")
    assert out["total_chunks"] == 2


@pytest.mark.asyncio
async def test_batch_images_no_dir(monkeypatch, tmp_path):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    import app.utils.path_tool as pt
    monkeypatch.setattr(pt, "get_data_path", lambda: str(tmp_path))
    out = await svc.handle_get_batch_images(USER_A, "m1")
    assert out["images"] == {}


@pytest.mark.asyncio
async def test_batch_images_read_files(monkeypatch, tmp_path):
    from app.router.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    import app.utils.path_tool as pt
    monkeypatch.setattr(pt, "get_data_path", lambda: str(tmp_path))
    img_dir = tmp_path / "extracted_images" / USER_A / "m1"
    img_dir.mkdir(parents=True)
    (img_dir / "a.png").write_bytes(b"\x89PNG fake")
    out = await svc.handle_get_batch_images(USER_A, "m1")
    assert "a.png" in out["images"]
    assert out["images"]["a.png"].startswith("data:image/png;base64,")


def test_get_knowledge_service_factory():
    from app.router.knowledge_service import KnowledgeService, get_knowledge_service
    assert isinstance(get_knowledge_service(), KnowledgeService)
