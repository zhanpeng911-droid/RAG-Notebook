from dataclasses import dataclass, asdict
from typing import Optional
import json


EVENT_RESPONSE = "response"
EVENT_ERROR = "error"
EVENT_DONE = "done"


@dataclass
class SSEEvent:
    """SSE（Server-Sent Events）事件数据模型。

    用于在文件上传、切片等异步任务中向前端推送实时进度信息。
    每个事件包含事件类型、进度消息、文件处理状态等字段，
    前端可根据 event_type 和 progress 等字段渲染进度条和状态提示。

    属性:
        event_type: 事件类型，如 "progress"、"response"、"error"、"done"。
        message: 事件描述消息，前端用于展示给用户。
        total_files: 待处理的文件总数。
        file_index: 当前正在处理的文件索引（从 0 开始）。
        filename: 当前正在处理的文件名。
        step: 当前处理阶段，如 "slicing"、"indexing" 等。
        progress: 总体进度百分比（0-100）。
        success_count: 已成功处理的文件数量。
        failed_count: 处理失败的文件数量。
        slice_success_count: 已成功切片的文件数量。
        error_message: 错误描述信息，仅在出错时填充。
        chunk_count: 当前文件生成的切片数量。
    """

    event_type: str
    message: str
    total_files: int = 0
    file_index: Optional[int] = None
    filename: Optional[str] = None
    step: Optional[str] = None
    progress: int = 0
    success_count: int = 0
    failed_count: int = 0
    slice_success_count: int = 0
    error_message: Optional[str] = None
    chunk_count: Optional[int] = None

    def to_sse(self) -> str:
        """将事件数据序列化为 SSE 协议格式的字符串。

        自动过滤值为 None 的字段，以减少传输数据量。
        输出格式遵循 SSE 规范：event 行 + data 行 + 空行。

        :return: 符合 SSE 协议格式的字符串，可直接写入 HTTP 响应流。
        """
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        return f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class SliceResult:
    """文件切片结果数据结构。

    在多线程文件切片流程中，每个文件切片完成后会生成一个 SliceResult 实例，
    通过 TaskQueue 传递给主写入线程。支持成功和失败两种状态，使用工厂方法
    创建以保证数据一致性。

    属性:
        file_index: 文件在上传列表中的索引位置。
        filename: 处理后的文件名。
        documents: 切片后的文档列表（LangChain Document 对象）。
        md5: 文件的 MD5 摘要值，用于去重判断。
        success: 切片是否成功。
        error: 切片失败时的错误描述信息。
        chunk_count: 成功切片后生成的文档块数量。
    """

    def __init__(self):
        """初始化一个空的切片结果实例，各字段使用默认值。"""
        self.file_index: int = 0
        self.filename: str = ""
        self.documents: list = []
        self.md5: str = ""
        self.success: bool = False
        self.error: Optional[str] = None
        self.chunk_count: int = 0

    @classmethod
    def success_result(cls, file_index: int, filename: str, documents: list, md5: str) -> 'SliceResult':
        """工厂方法：创建切片成功的结果实例。

        :param file_index: 文件在上传列表中的索引。
        :param filename: 处理后的文件名。
        :param documents: 切片后的文档列表。
        :param md5: 文件的 MD5 摘要值。
        :return: 已填充成功状态的 SliceResult 实例。
        """
        result = cls()
        result.file_index = file_index
        result.filename = filename
        result.documents = documents
        result.md5 = md5
        result.success = True
        result.chunk_count = len(documents)
        return result

    @classmethod
    def error_result(cls, file_index: int, filename: str, error: str) -> 'SliceResult':
        """工厂方法：创建切片失败的结果实例。

        :param file_index: 文件在上传列表中的索引。
        :param filename: 处理后的文件名。
        :param error: 切片失败的错误描述信息。
        :return: 已填充失败状态的 SliceResult 实例。
        """
        result = cls()
        result.file_index = file_index
        result.filename = filename
        result.success = False
        result.error = error
        return result

    def to_dict(self) -> dict:
        """将切片结果转换为字典格式，便于 JSON 序列化和日志记录。

        :return: 包含所有字段的字典，键名与属性名一致。
        """
        return {
            'file_index': self.file_index,
            'filename': self.filename,
            'documents': self.documents,
            'md5': self.md5,
            'success': self.success,
            'error': self.error,
            'chunk_count': self.chunk_count
        }
