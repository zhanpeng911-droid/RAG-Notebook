"""
SSE 事件构造测试 —— 验证所有 SSE 事件的字段完整性和格式兼容性。

覆盖风险点：
- start event 包含 event_type=start、total_files、progress=0
- finish event 包含 event_type=finish、progress=100
- 所有事件格式为 SSE 字符串（data: {...}\n\n）
"""
import time
import json
import pytest
from unittest.mock import patch, MagicMock


def _make_mock_sse_event(**kwargs):
    """创建模拟 SSEEvent，返回真实 SSE 字符串"""
    class _SSE:
        def __init__(self, **kw):
            self._data = kw
        def to_sse(self):
            return f"data: {json.dumps(self._data, ensure_ascii=False)}\n\n"
    return _SSE(**kwargs)


def _parse_sse(raw: str) -> dict:
    """从 SSE 字符串中解析 JSON payload"""
    for line in raw.strip().split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise ValueError(f"无法解析 SSE: {raw}")


# ==================== start event ====================

def test_start_event_fields():
    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_start_event
        raw = build_start_event(total_files=5)
    payload = _parse_sse(raw)
    assert payload["event_type"] == "start"
    assert payload["total_files"] == 5
    assert payload["progress"] == 0
    assert "message" in payload


# ==================== size error event ====================

def test_size_error_event_fields():
    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_size_error_event
        raw = build_size_error_event()
    payload = _parse_sse(raw)
    assert payload["event_type"] == "error"
    assert "200MB" in payload["message"]


# ==================== validation error event ====================

def test_validation_error_event_fields():
    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_validation_error_event
        raw = build_validation_error_event(
            current_index=2, total_files=5, filename="bad.exe",
            file_type="application/octet-stream", file_extension=".exe", failed_count=1
        )
    payload = _parse_sse(raw)
    assert payload["event_type"] == "error"
    assert payload["step"] == "validation"
    assert payload["filename"] == "bad.exe"
    assert payload["failed_count"] == 1
    assert payload["file_index"] == 2
    assert payload["total_files"] == 5


# ==================== slicing completed event ====================

def test_slicing_completed_event_fields():
    result = MagicMock()
    result.file_index = 1
    result.filename = "test.pdf"
    result.chunk_count = 10

    state = MagicMock()
    state.total_files = 3
    state.current_progress.return_value = 60
    state.success_count = 0
    state.failed_count = 0
    state.slice_success_count = 1

    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_slicing_completed_event
        raw = build_slicing_completed_event(result, state)
    payload = _parse_sse(raw)
    assert payload["event_type"] == "slicing_completed"
    assert payload["step"] == "slicing"
    assert payload["chunk_count"] == 10
    assert payload["progress"] == 60


# ==================== writing event ====================

def test_writing_event_fields():
    result = MagicMock()
    result.file_index = 1
    result.filename = "test.pdf"

    state = MagicMock()
    state.total_files = 3
    state.current_progress.return_value = 70
    state.success_count = 0
    state.failed_count = 0
    state.slice_success_count = 1

    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_writing_event
        raw = build_writing_event(result, state)
    payload = _parse_sse(raw)
    assert payload["event_type"] == "writing"
    assert payload["step"] == "writing"


# ==================== completed event ====================

def test_completed_event_fields():
    result = MagicMock()
    result.file_index = 1
    result.filename = "test.pdf"

    state = MagicMock()
    state.total_files = 3
    state.current_progress.return_value = 80
    state.success_count = 1
    state.failed_count = 0
    state.slice_success_count = 1

    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_completed_event
        raw = build_completed_event(result, state)
    payload = _parse_sse(raw)
    assert payload["event_type"] == "completed"
    assert payload["step"] == "completed"


# ==================== write error event ====================

def test_write_error_event_fields():
    result = MagicMock()
    result.file_index = 1
    result.filename = "test.pdf"

    state = MagicMock()
    state.total_files = 3
    state.current_progress.return_value = 50
    state.success_count = 0
    state.failed_count = 1
    state.slice_success_count = 1

    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_write_error_event
        raw = build_write_error_event(result, state, "write timeout")
    payload = _parse_sse(raw)
    assert payload["event_type"] == "error"
    assert payload["step"] == "writing"
    assert payload["error_message"] == "write timeout"


# ==================== slice error event ====================

def test_slice_error_event_fields():
    result = MagicMock()
    result.file_index = 1
    result.filename = "test.pdf"
    result.error = "empty file"

    state = MagicMock()
    state.total_files = 3
    state.current_progress.return_value = 30
    state.success_count = 0
    state.failed_count = 1
    state.slice_success_count = 0

    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_slice_error_event
        raw = build_slice_error_event(result, state)
    payload = _parse_sse(raw)
    assert payload["event_type"] == "error"
    assert payload["step"] == "slicing"
    assert payload["error_message"] == "empty file"


# ==================== finish event ====================

def test_finish_event_fields():
    start_time = time.time() - 5.0
    with patch("app.services.knowledge_sse_events.SSEEvent", _make_mock_sse_event):
        from app.services.knowledge_sse_events import build_finish_event
        raw = build_finish_event(start_time, total_files=3, success_count=2, failed_count=1)
    payload = _parse_sse(raw)
    assert payload["event_type"] == "finish"
    assert payload["progress"] == 100
    assert payload["total_files"] == 3
    assert payload["success_count"] == 2
    assert payload["failed_count"] == 1
    assert "耗时" in payload["message"]
