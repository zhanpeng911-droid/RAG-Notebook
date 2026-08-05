"""
知识库文件校验 —— 文件类型、大小、MIME 校验。

职责：
- 定义允许的文件扩展名和 MIME 类型
- 定义文件大小限制（单文件 20MB，总文件 200MB）
- 检测文件 MIME 类型（libmagic 优先，扩展名回退）
- 安全获取文件名（处理 None 和 bytes）
"""
import mimetypes
from typing import Optional

from fastapi import UploadFile

from app.core.logger_handler import logger

# 惰性导入 magic，避免 Windows 上 python-magic segfault 导致模块加载失败
_magic_module = None
_magic_imported = False


def _get_magic():
    """python-magic 在 Windows 上会 segfault，直接返回 None 禁用。"""
    return None

# ==================== 文件类型常量 ====================

ALLOWED_EXTENSIONS = {'.pdf', '.txt', '.md', '.pptx', '.docx'}
ALLOWED_MIME_TYPES = {
    'application/pdf', 'text/plain', 'text/markdown',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
}

# ==================== 文件大小常量 ====================

MAX_FILE_SIZE = 20 * 1024 * 1024      # 单文件 20MB
MAX_FOLDER_SIZE = 200 * 1024 * 1024   # 总文件 200MB


def safe_filename(file: UploadFile) -> str:
    """安全获取文件名，处理 None 和 bytes 类型"""
    if file.filename is None:
        return "unknown"
    if isinstance(file.filename, bytes):
        return file.filename.decode()
    return file.filename


def detect_file_type(content: bytes, filename: str) -> str:
    """
    检测文件的 MIME 类型。

    优先使用 libmagic（python-magic）从文件内容字节进行检测，若检测失败或库不可用，
    则回退到根据文件扩展名推断 MIME 类型。

    参数:
        content (bytes): 文件的原始字节内容，用于 libmagic 分析。
        filename (str): 文件名，用于扩展名回退检测。

    返回:
        str: 检测到的 MIME 类型字符串，如 'application/pdf'、'text/plain'。
             若均无法识别，返回 'application/octet-stream'。
    """
    _magic = _get_magic()
    if _magic is not None:
        try:
            return _magic.Magic(mime=True).from_buffer(content)
        except Exception as e:
            logger.warning(f"libmagic 检测失败，回退到扩展名检测: {e}")
    return mimetypes.guess_type(filename)[0] or 'application/octet-stream'


def is_allowed_file(content: bytes, filename: str) -> bool:
    """检查文件类型是否在允许列表中（MIME 或扩展名任一匹配即可）"""
    file_type = detect_file_type(content, filename)
    file_extension = _file_extension(filename)
    return file_type in ALLOWED_MIME_TYPES or file_extension in ALLOWED_EXTENSIONS


def validate_file_type(content: bytes, filename: str) -> Optional[str]:
    """
    校验文件类型，返回错误信息；通过则返回 None。
    """
    file_type = detect_file_type(content, filename)
    file_extension = _file_extension(filename)
    if file_type not in ALLOWED_MIME_TYPES and file_extension not in ALLOWED_EXTENSIONS:
        return f"文件类型不支持，目前支持PDF、TXT、Markdown、PPTX、DOCX文件类型。检测到的文件类型: {file_type}，扩展名: {file_extension}"
    return None


def validate_single_file_size(file: UploadFile) -> Optional[str]:
    """校验单文件大小，返回错误信息；通过则返回 None"""
    if file.size is not None and file.size > MAX_FILE_SIZE:
        return "文件大小不能超过20MB"
    return None


def validate_total_size(total_size: int) -> Optional[str]:
    """校验文件总大小，返回错误信息；通过则返回 None"""
    if total_size > MAX_FOLDER_SIZE:
        return "文件总大小不能超过200MB"
    return None


def _file_extension(filename: str) -> str:
    """获取小写的文件扩展名"""
    import os
    return os.path.splitext(filename)[1].lower()
