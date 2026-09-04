"""failed_response 测试 —— 全局异常处理器的状态码/脱敏/友好文案。

直接构造 starlette Request 与异常实例调用 handler，验证响应契约；
register_exception_handlers 用假 app 断言注册数量。
"""
import json
import importlib

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError

from app.core import failed_response as fr
from app.core.failed_response import (
    BusinessException,
    mask_sensitive_info,
)
from app.core.failed_response_register import register_exception_handlers


def _request(path="/api/test", method="POST"):
    from starlette.requests import Request
    scope = {
        "type": "http", "method": method, "path": path,
        "raw_path": path.encode(), "query_string": b"",
        "headers": [], "scheme": "http", "root_path": "",
        "server": ("testserver", 80), "client": ("1.2.3.4", 1),
    }
    return Request(scope)


def _body(resp):
    return json.loads(resp.body)


# ---------- 脱敏 ----------

def test_mask_sensitive_info_patterns():
    text = ("sk-" + "a" * 40 + " password='hunter2' "
            "api_key=\"sk-live-abcdefghijklmnopqrstuvwxyz123456\" mysql://u:p@host/db")
    out = mask_sensitive_info(text)
    assert ("a" * 40) not in out
    assert "hunter2" not in out
    assert "sk-live-abcdefghijklmnopqrstuvwxyz123456" not in out
    assert "u:p@host" not in out      # mysql 连接串已整体脱敏
    # 兼容旧写法（password 后直接引号再等号）
    old_style = mask_sensitive_info("password='secret'=x")
    assert "secret" not in old_style
    assert mask_sensitive_info("") == ""


# ---------- 各 handler ----------

@pytest.mark.asyncio
async def test_business_exception_handler():
    exc = BusinessException(code=4001, message="配额不足")
    resp = await fr.business_exception_handler(_request(), exc)
    assert resp.status_code == 200
    assert _body(resp) == {"code": 4001, "message": "配额不足", "data": None}


@pytest.mark.asyncio
async def test_http_exception_handler_maps_known_codes():
    for code, expect in [(401, "请先登录"), (403, "无权限"), (404, "接口不存在"),
                         (405, "请求方法不支持"), (429, "请求过于频繁")]:
        resp = await fr.http_exception_handler(
            _request(), HTTPException(status_code=code))
        body = _body(resp)
        assert body["code"] == code and expect in body["message"]
        assert resp.status_code == code


@pytest.mark.asyncio
async def test_http_exception_handler_unknown_code_uses_detail():
    resp = await fr.http_exception_handler(
        _request(), HTTPException(status_code=418, detail="自定消息"))
    body = _body(resp)
    assert body["code"] == 418 and body["message"] == "自定消息"


@pytest.mark.asyncio
async def test_http_exception_handler_preserves_retry_after_and_www_authenticate():
    """429 Retry-After 与 401 WWW-Authenticate 必须在异常处理响应中保留（F10）。"""
    resp429 = await fr.http_exception_handler(
        _request(), HTTPException(status_code=429, detail="限流", headers={"Retry-After": "60"}))
    assert resp429.status_code == 429
    assert resp429.headers.get("Retry-After") == "60"

    resp401 = await fr.http_exception_handler(
        _request(), HTTPException(status_code=401, detail="未登录",
                                 headers={"WWW-Authenticate": "Bearer"}))
    assert resp401.status_code == 401
    assert resp401.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_validation_exception_handler_friendly_fields():
    exc = RequestValidationError([
        {"loc": ("body", "age"), "msg": "field required", "type": "missing"},
        {"loc": ("body", "count"), "msg": "invalid", "type": "int_parsing"},
    ])
    resp = await fr.validation_exception_handler(_request(), exc)
    assert resp.status_code == 400
    body = _body(resp)
    assert "字段「age」为必填项" in body["message"]
    assert "字段「count」应为整数类型" in body["message"]
    assert body["data"]["error_type"] == "RequestValidationError"


@pytest.mark.asyncio
async def test_integrity_error_handler_friendly_branches():
    dupe = IntegrityError("stmt", {}, Exception("Duplicate entry 'x' for key 'username_UNIQUE'"))
    resp = await fr.integrity_error_handler(_request(), dupe)
    assert _body(resp)["message"] == "用户名已存在"

    fk = IntegrityError("stmt", {}, Exception("FOREIGN KEY constraint failed"))
    resp2 = await fr.integrity_error_handler(_request(), fk)
    assert _body(resp2)["message"] == "关联数据不存在或当前用户无权限"

    generic = IntegrityError("stmt", {}, Exception("other constraint"))
    resp3 = await fr.integrity_error_handler(_request(), generic)
    assert _body(resp3)["message"] == "数据库完整性约束错误"


@pytest.mark.asyncio
async def test_sqlalchemy_error_handler_masks_traceback():
    from sqlalchemy.exc import SQLAlchemyError
    exc = SQLAlchemyError("boom with sk-" + "b" * 40)
    resp = await fr.sqlalchemy_error_handler(_request(), exc)
    assert resp.status_code == 500
    body = _body(resp)
    assert "数据库操作失败" in body["message"]
    detail = body["data"]["error_detail"]
    assert ("b" * 40) not in detail


@pytest.mark.asyncio
async def test_general_exception_handler_500():
    resp = await fr.general_exception_handler(_request(), RuntimeError("崩溃"))
    assert resp.status_code == 500
    body = _body(resp)
    assert "服务器内部错误" in body["message"]
    assert body["data"]["error_type"] == "RuntimeError"
    assert "崩溃" in body["data"]["error_detail"]


@pytest.mark.asyncio
async def test_rag_exception_handler():
    from app.core.exceptions import RAGException

    exc = RAGException(code=503, message="向量库不可用")
    resp = await fr.rag_exception_handler(_request(), exc)
    assert resp.status_code == 503
    assert _body(resp)["message"] == "向量库不可用"


# ---------- 注册 ----------

def test_register_exception_handlers_covers_expected_types():
    class FakeApp:
        def __init__(self):
            self.handlers = []

        def add_exception_handler(self, exc, handler):
            self.handlers.append(exc)

    app = FakeApp()
    register_exception_handlers(app)
    names = {getattr(t, "__name__", str(t)) for t in app.handlers}
    assert "BusinessException" in names
    assert "HTTPException" in names
    assert "RequestValidationError" in names
    assert "IntegrityError" in names
    assert "SQLAlchemyError" in names
    assert "RAGException" in names
    assert "Exception" in names


# ---------- 生产环境脱敏（实际加载环境配置，复现 prod/production 混用根因） ----------

@pytest.fixture
def reload_fr_with_env(monkeypatch):
    """按给定环境变量重新加载 failed_response 模块，验证后恢复默认。

    直接 reload 模块让模块级 settings/DEBUG_MODE 从真实环境重新计算，
    避免只 monkeypatch 最终 DEBUG_MODE 常量而漏掉根因。
    """
    from app.config import validator

    def _apply(**env):
        for key in ("ENV", "DEBUG_MODE", "LOG_LEVEL"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        validator.get_settings.cache_clear()
        importlib.reload(fr)

    yield _apply

    validator.get_settings.cache_clear()
    importlib.reload(fr)


@pytest.mark.parametrize("env", ["prod", "production", "PRODUCTION"])
def test_production_forces_debug_off_even_if_debug_true(reload_fr_with_env, env):
    reload_fr_with_env(ENV=env, DEBUG_MODE=True)

    assert fr.DEBUG_MODE is False

    exc = RuntimeError("秘密串 sk-" + "c" * 40)
    import asyncio
    resp = asyncio.run(fr.general_exception_handler(_request(), exc))
    body = _body(resp)
    assert body["data"] is None
    assert "sk-" not in json.dumps(body)

    val_resp = asyncio.run(fr.validation_exception_handler(
        _request(), RequestValidationError(
            [{"loc": ("body", "age"), "msg": "field required", "type": "missing"}])))
    val_body = _body(val_resp)
    assert val_body["data"] is None  # 不返回 raw_errors / path


def test_dev_keeps_debug_details_when_debug_true(reload_fr_with_env):
    reload_fr_with_env(ENV="dev", DEBUG_MODE=True)
    assert fr.DEBUG_MODE is True

    import asyncio
    resp = asyncio.run(fr.general_exception_handler(_request(), RuntimeError("崩溃")))
    body = _body(resp)
    assert body["data"] is not None
    assert body["data"]["error_type"] == "RuntimeError"


def test_dev_debug_off_masks_details(reload_fr_with_env):
    reload_fr_with_env(ENV="dev", DEBUG_MODE=False)
    assert fr.DEBUG_MODE is False

    import asyncio
    resp = asyncio.run(fr.general_exception_handler(_request(), RuntimeError("崩溃")))
    assert _body(resp)["data"] is None

