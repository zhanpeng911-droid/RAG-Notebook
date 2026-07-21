"""请求上下文（request_id 等）。"""
from contextvars import ContextVar
from typing import Optional

_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(request_id: str | None) -> None:
    _request_id_var.set(request_id)


def get_request_id() -> str | None:
    return _request_id_var.get()
