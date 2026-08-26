"""MD5Store 单元测试 —— 去重命中/未命中、用户隔离、损坏恢复与删除。

构造器从 chroma_config 读路径（conftest 下为 mock），这里用
__new__ 绕过并把 base_dir 指到 pytest 临时目录，专注测文件逻辑。
"""
import json
import os

import pytest

from app.rag.md5_manager.md5_store import MD5Store


@pytest.fixture()
def store(tmp_path):
    s = MD5Store.__new__(MD5Store)
    s.base_dir = str(tmp_path)
    return s


@pytest.mark.asyncio
async def test_check_md5_missing_dir_creates_and_returns_false(store, tmp_path):
    assert await store.check_md5_hex("abc", user_id="u1") is False
    # 首次检查会自动建目录和空记录文件
    assert os.path.isfile(str(tmp_path / "user_md5" / "u1" / "md5_hex_store.txt"))


@pytest.mark.asyncio
async def test_save_then_check_hit(store):
    await store.save_md5_hex("m-123", filename="a.pdf",
                             original_filename="原始.pdf", user_id="u1")
    assert await store.check_md5_hex("m-123", user_id="u1") is True
    assert await store.check_md5_hex("other", user_id="u1") is False


@pytest.mark.asyncio
async def test_user_isolation_between_users(store):
    await store.save_md5_hex("m-user", user_id="alice")
    assert await store.check_md5_hex("m-user", user_id="bob") is False


@pytest.mark.asyncio
async def test_public_store_isolated_from_user_store(store):
    await store.save_md5_hex("m-pub", user_id=None)
    assert await store.check_md5_hex("m-pub", user_id=None) is True
    assert await store.check_md5_hex("m-pub", user_id="someone") is False


def test_save_sync_visible_to_async_read(store):
    store.save_md5_hex_sync("m-sync", filename="b.docx", user_id="carol")
    import asyncio
    _, rows = asyncio.run(store._read_md5_records(user_id="carol"))
    assert len(rows) == 1
    assert rows[0]["md5"] == "m-sync"
    assert rows[0]["filename"] == "b.docx"
    assert rows[0]["upload_time"] is not None


@pytest.mark.asyncio
async def test_legacy_plain_text_line_format_supported(store):
    d = store._get_md5_store_dir("old")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "md5_hex_store.txt"), "w", encoding="utf-8") as f:
        f.write("legacy-md5-line\n")
    assert await store.check_md5_hex("legacy-md5-line", user_id="old") is True


@pytest.mark.asyncio
async def test_corrupted_json_line_falls_back_to_raw_compare(store):
    d = store._get_md5_store_dir("bad")
    os.makedirs(d, exist_ok=True)
    broken = "{not-valid-json"
    with open(os.path.join(d, "md5_hex_store.txt"), "w", encoding="utf-8") as f:
        f.write(broken + "\n")
    # 解析失败后按纯文本整行比对，不崩溃也不误报
    assert await store.check_md5_hex(broken, user_id="bad") is True
    assert await store.check_md5_hex("different", user_id="bad") is False


@pytest.mark.asyncio
async def test_get_md5_info_found_and_missing(store):
    await store.save_md5_hex("m-i", filename="i.txt", user_id="dave")
    info = await store.get_md5_info("dave", "m-i")
    assert info["original_filename"] is None
    assert await store.get_md5_info("dave", "nope") is None


@pytest.mark.asyncio
async def test_get_all_records_empty_when_file_absent(store):
    assert await store.get_all_md5_records("nobody") == []


@pytest.mark.asyncio
async def test_delete_by_filename_removes_and_returns_md5(store):
    await store.save_md5_hex("m-del", filename="gone.txt", user_id="erin")
    await store.save_md5_hex("m-keep", filename="stay.txt", user_id="erin")
    got = await store.delete_by_filename("erin", "gone.txt")
    assert got == "m-del"
    remaining = await store.get_all_md5_records("erin")
    assert [r["md5"] for r in remaining] == ["m-keep"]
    # 再删一次：已无匹配
    assert await store.delete_by_filename("erin", "gone.txt") is None


@pytest.mark.asyncio
async def test_delete_single_md5_true_false_paths(store):
    await store.save_md5_hex("m-1", user_id="frank")
    assert await store.delete_single_md5("frank", "m-1") is True
    assert await store.delete_single_md5("frank", "m-1") is False
    assert await store.delete_single_md5("stranger", "m-1") is False


@pytest.mark.asyncio
async def test_delete_user_md5_removes_directory(store, tmp_path):
    await store.save_md5_hex("m-x", user_id="grace")
    user_dir = tmp_path / "user_md5" / "grace"
    assert user_dir.exists()
    await store.delete_user_md5("grace")
    assert not user_dir.exists()


@pytest.mark.asyncio
async def test_write_empty_records_cleans_file_and_dir(store, tmp_path):
    await store.save_md5_hex("m-c", user_id="heidi")
    md5_path, records = await store._read_md5_records("heidi")
    assert len(records) == 1
    await store._write_md5_records(md5_path, [])
    assert not os.path.exists(md5_path)
    assert not (tmp_path / "user_md5" / "heidi").exists()


@pytest.mark.asyncio
async def test_read_records_tolerates_legacy_and_broken_lines(store):
    d = store._get_md5_store_dir("mix")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "md5_hex_store.txt"), "w", encoding="utf-8") as f:
        f.write("plain-md5\n" + "{broken-json\n" + "\n")
        f.write(json.dumps({"md5": "good", "filename": "f"}) + "\n")
    rows = await store.get_all_md5_records("mix")
    assert [r["md5"] for r in rows] == ["plain-md5", "{broken-json", "good"]
    assert all(r["filename"] is None or r["md5"] == "good" for r in rows)


def test_dir_layout_public_vs_user(store):
    assert store._get_md5_store_dir(None).endswith("public_md5")
    assert "user_md5" in store._get_md5_store_dir("u9")
