"""image_extractor 单元测试 —— 图片存储目录、PDF 提取与清理。

提取/写入走真实 PyMuPDF：临时目录内构造含一张嵌入 PNG 的最小 PDF；
数据根目录通过替换模块级 get_data_path 隔离到 pytest tmp。
"""
import base64
import os

import pytest

from tests.helpers.unmock import restore_real

# conftest 预注册的假条目会短路父包绑定
restore_real("app.utils.image_extractor")

import app  # noqa: E402,F401
import app.utils  # noqa: E402,F401
import app.utils.image_extractor as ie  # noqa: E402
from app.utils.image_extractor import (  # noqa: E402
    delete_image_directory,
    delete_user_all_images,
    extract_images_from_pdf,
    get_image_storage_dir,
)

# 1x1 白色 PNG
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def data_root(monkeypatch, tmp_path):
    monkeypatch.setattr(ie, "get_data_path", lambda: str(tmp_path))
    return tmp_path


def _make_pdf_with_one_image(path):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_PNG_1PX)
    doc.save(str(path))
    doc.close()


def test_get_image_storage_dir_creates_nested(data_root):
    d = get_image_storage_dir("u1", "MD5A")
    assert os.path.isdir(d)
    assert str(data_root / "extracted_images" / "u1" / "MD5A") == d


def test_extract_missing_pdf_returns_empty(data_root):
    assert extract_images_from_pdf("ghost.pdf", "u1", "M") == {}


def test_extract_from_real_pdf_with_embedded_png(data_root, tmp_path):
    pdf = tmp_path / "sample.pdf"
    _make_pdf_with_one_image(pdf)

    result = extract_images_from_pdf(str(pdf), "u1", "MD5B")
    assert list(result.keys()) == [0]
    names = result[0]
    assert len(names) == 1
    saved = data_root / "extracted_images" / "u1" / "MD5B" / names[0]
    assert saved.is_file() and saved.stat().st_size > 0
    assert names[0].startswith("p0_i0.") and names[0].endswith(".png")


def test_extract_unopenable_file_returns_empty(data_root, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    # PyMuPDF 打不开的文件同样走异常兜底
    assert extract_images_from_pdf(str(bad), "u1", "M") == {}


def test_delete_image_directory_true_false(data_root):
    assert delete_image_directory("u1", "NOPE") is False
    d = get_image_storage_dir("u1", "HAS")
    with open(os.path.join(d, "p0_i0.png"), "wb") as fh:
        fh.write(b"x")
    assert delete_image_directory("u1", "HAS") is True
    assert not os.path.exists(d)


def test_delete_user_all_images_true_false(data_root):
    assert delete_user_all_images("nobody") is False
    get_image_storage_dir("user9", "A")
    get_image_storage_dir("user9", "B")
    assert delete_user_all_images("user9") is True
    user_dir = data_root / "extracted_images" / "user9"
    assert not user_dir.exists()
