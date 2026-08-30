"""
文件校验模块测试 —— 验证文件类型、大小、MIME 校验逻辑。

覆盖风险点：
- 允许的扩展名通过
- 不允许的扩展名失败
- safe_filename 处理 None/bytes
- 单文件大小限制
- 总文件大小限制

注意：python-magic 在 Windows 上可能 segfault，MIME 相关测试仅在非 Windows 平台执行。
"""
import sys
import pytest
from unittest.mock import MagicMock

_skip_magic = sys.platform == "win32"

from app.services.knowledge_file_validator import (  # noqa: E402 -- 需先按平台决定 _skip_magic
    safe_filename, detect_file_type, is_allowed_file, validate_file_type,
    validate_single_file_size, validate_total_size,
    ALLOWED_EXTENSIONS, MAX_FILE_SIZE, MAX_FOLDER_SIZE,
)


# ==================== safe_filename ====================

def test_safe_filename_normal():
    file = MagicMock()
    file.filename = "test.pdf"
    assert safe_filename(file) == "test.pdf"


def test_safe_filename_none():
    file = MagicMock()
    file.filename = None
    assert safe_filename(file) == "unknown"


def test_safe_filename_bytes():
    file = MagicMock()
    file.filename = b"test.pdf"
    assert safe_filename(file) == "test.pdf"


# ==================== detect_file_type ====================

@pytest.mark.skipif(_skip_magic, reason="python-magic 在 Windows 上 segfault，仅 Linux/macOS 执行")
def test_detect_file_type_returns_string():
    result = detect_file_type(b"test content", "test.txt")
    assert isinstance(result, str)
    assert len(result) > 0


# ==================== is_allowed_file ====================

@pytest.mark.skipif(_skip_magic, reason="python-magic 在 Windows 上 segfault，仅 Linux/macOS 执行")
def test_allowed_extensions():
    for ext in ALLOWED_EXTENSIONS:
        assert is_allowed_file(b"content", f"file{ext}")


@pytest.mark.skipif(_skip_magic, reason="python-magic 在 Windows 上 segfault，仅 Linux/macOS 执行")
def test_disallowed_extension():
    assert not is_allowed_file(b"content", "file.exe")


@pytest.mark.skipif(_skip_magic, reason="python-magic 在 Windows 上 segfault，仅 Linux/macOS 执行")
def test_disallowed_script():
    assert not is_allowed_file(b"content", "file.bat")


# ==================== validate_file_type ====================

@pytest.mark.skipif(_skip_magic, reason="python-magic 在 Windows 上 segfault，仅 Linux/macOS 执行")
def test_validate_file_type_passes_for_allowed():
    for ext in ALLOWED_EXTENSIONS:
        result = validate_file_type(b"content", f"file{ext}")
        assert result is None, f"扩展名 {ext} 应该通过校验"


@pytest.mark.skipif(_skip_magic, reason="python-magic 在 Windows 上 segfault，仅 Linux/macOS 执行")
def test_validate_file_type_fails_for_disallowed():
    result = validate_file_type(b"content", "file.exe")
    assert result is not None
    assert "不支持" in result


# ==================== validate_single_file_size ====================

def test_single_file_size_passes():
    file = MagicMock()
    file.size = 1024
    assert validate_single_file_size(file) is None


def test_single_file_size_fails():
    file = MagicMock()
    file.size = MAX_FILE_SIZE + 1
    result = validate_single_file_size(file)
    assert result is not None
    assert "20MB" in result


def test_single_file_size_none():
    file = MagicMock()
    file.size = None
    assert validate_single_file_size(file) is None


# ==================== validate_total_size ====================

def test_total_size_passes():
    assert validate_total_size(1000) is None


def test_total_size_fails():
    result = validate_total_size(MAX_FOLDER_SIZE + 1)
    assert result is not None
    assert "200MB" in result


# ==================== 常量值验证 ====================

def test_max_file_size_is_20mb():
    assert MAX_FILE_SIZE == 20 * 1024 * 1024


def test_max_folder_size_is_200mb():
    assert MAX_FOLDER_SIZE == 200 * 1024 * 1024


# ==================== is_valid_md5（路径穿越防护回归） ====================

from app.services.knowledge_file_validator import is_valid_md5  # noqa: E402


def test_is_valid_md5_accepts_32hex():
    assert is_valid_md5("a" * 32) is True
    assert is_valid_md5("0123456789abcdef0123456789abcdef") is True
    assert is_valid_md5("A" * 32) is True  # 大写也接受（内部 lower）


def test_is_valid_md5_rejects_traversal_and_garbage():
    assert is_valid_md5("../evil") is False
    assert is_valid_md5(".." + chr(92) + ".." + chr(92) + "x") is False
    assert is_valid_md5("") is False
    assert is_valid_md5(None) is False
    assert is_valid_md5("a" * 31) is False
    assert is_valid_md5("g" * 32) is False
