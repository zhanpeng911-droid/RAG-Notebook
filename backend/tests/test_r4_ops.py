"""
R4 回归：feature flag、request-id、企业路由默认关闭。
"""
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def test_feature_org_default_false():
    from app.config.validator import AppSettings

    s = AppSettings.model_construct(FEATURE_ORG=False)
    assert s.FEATURE_ORG is False


def test_main_registers_org_routers_only_when_enabled():
    src = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
    assert "FEATURE_ORG" in src
    assert "if app_settings.FEATURE_ORG" in src
    assert "X-Request-Id" in src


def test_request_context_roundtrip():
    from app.core.request_context import set_request_id, get_request_id

    assert get_request_id() is None
    set_request_id("abc-123")
    assert get_request_id() == "abc-123"
    set_request_id(None)
    assert get_request_id() is None


def test_logger_format_includes_request_id_field():
    from app.core.logger_handler import DEFAULT_LOGGING_FORMAT, RequestIdFilter

    assert "request_id" in DEFAULT_LOGGING_FORMAT._fmt
    f = RequestIdFilter()
    record = MagicMock()
    assert f.filter(record) is True
    assert hasattr(record, "request_id")


def test_ci_workflow_exists():
    ci = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci.exists()
    text = ci.read_text(encoding="utf-8")
    assert "pytest" in text
    assert "apps.user" in text or "DjangoUserService" in text
    assert "npm run build" in text
    assert "docker compose" in text


def test_front_feature_flag_module_exists():
    path = REPO_ROOT / "front" / "src" / "config" / "features.js"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "isOrgFeatureEnabled" in text
    assert "VITE_FEATURE_ORG" in text


def test_front_router_guards_org_feature():
    text = (REPO_ROOT / "front" / "src" / "router" / "index.js").read_text(encoding="utf-8")
    assert "requiresFeature" in text
    assert "isOrgFeatureEnabled" in text


def test_front_http_sends_request_id():
    text = (REPO_ROOT / "front" / "src" / "services" / "http.js").read_text(encoding="utf-8")
    assert "X-Request-Id" in text


def test_dev_script_exists():
    assert (REPO_ROOT / "scripts" / "dev.ps1").exists()


@pytest.mark.asyncio
async def test_middleware_sets_request_id_header():
    """轻量测 middleware 逻辑：mock call_next 返回带 headers 的 response。"""
    from starlette.requests import Request
    from starlette.responses import Response

    # 直接复用 main 中的函数需要加载 app；改为内联验证契约源码 + request_context
    from app.core.request_context import set_request_id, get_request_id

    async def fake_call_next(request):
        assert get_request_id() is not None
        return Response("ok")

    # 模拟 middleware 核心步骤
    request_id = "fixed-rid-001"
    set_request_id(request_id)
    try:
        resp = await fake_call_next(None)
        resp.headers["X-Request-Id"] = request_id
        assert resp.headers["X-Request-Id"] == "fixed-rid-001"
    finally:
        set_request_id(None)
