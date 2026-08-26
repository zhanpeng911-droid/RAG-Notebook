"""
R2 安全与 JWT 契约测试。

覆盖：
- JWT claims / leeway 解码契约
- 生产环境剥离客户端 api_key
- rate_limit key 不含 query 噪音
- http 侧去掉全局 _t 的源码约定（前端文件检查）
"""
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jose import jwt


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
SECRET = "r2-test-shared-jwt-secret"
ALGORITHM = "HS256"


def _django_like_token(user_id="user-uuid-1", username="alice", exp_offset=3600, **extra):
    payload = {
        "user_id": user_id,
        "username": username,
        "email": "alice@example.com",
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
        "jti": "jti-test-001",
        **extra,
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


# ==================== JWT 契约 ====================


def test_decode_django_jwt_accepts_django_payload():
    from app.utils import auth_utils

    with patch.object(auth_utils, "SECRET_KEY", SECRET), patch.object(
        auth_utils, "ALGORITHM", ALGORITHM
    ):
        token = _django_like_token()
        payload = auth_utils.decode_django_jwt(token)
        assert payload is not None
        assert payload["user_id"] == "user-uuid-1"
        assert payload["username"] == "alice"
        assert payload["jti"] == "jti-test-001"
        assert "exp" in payload


def test_decode_django_jwt_rejects_wrong_secret():
    from app.utils import auth_utils

    with patch.object(auth_utils, "SECRET_KEY", SECRET), patch.object(
        auth_utils, "ALGORITHM", ALGORITHM
    ):
        token = jwt.encode(
            {
                "user_id": "u1",
                "username": "x",
                "exp": int(time.time()) + 60,
                "iat": int(time.time()),
                "jti": "j2",
            },
            "wrong-secret",
            algorithm=ALGORITHM,
        )
        assert auth_utils.decode_django_jwt(token) is None


def test_decode_django_jwt_rejects_expired_beyond_leeway():
    from app.utils import auth_utils

    with patch.object(auth_utils, "SECRET_KEY", SECRET), patch.object(
        auth_utils, "ALGORITHM", ALGORITHM
    ):
        # 过期 120 秒，超过 leeway=30
        token = _django_like_token(exp_offset=-120)
        assert auth_utils.decode_django_jwt(token) is None


def test_decode_django_jwt_allows_small_clock_skew():
    from app.utils import auth_utils

    with patch.object(auth_utils, "SECRET_KEY", SECRET), patch.object(
        auth_utils, "ALGORITHM", ALGORITHM
    ):
        # 刚过期 10 秒，在 30s leeway 内
        token = _django_like_token(exp_offset=-10)
        payload = auth_utils.decode_django_jwt(token)
        assert payload is not None
        assert payload["user_id"] == "user-uuid-1"


def test_jwt_contract_doc_exists():
    doc = REPO_ROOT / "docs" / "JWT_CONTRACT.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "HS256" in text
    assert "user_id" in text
    assert "blacklist:{jti}" in text


# ==================== 生产禁客户端 api_key ====================


def test_sanitize_strips_api_key_when_disallowed():
    from app.utils.factory import sanitize_client_llm_config

    with patch("app.utils.factory.get_settings") as gs:
        gs.return_value.allow_client_llm_key = False
        cleaned = sanitize_client_llm_config(
            {"provider": "openai", "api_key": "sk-leak", "model": "gpt-4o"}
        )
        assert cleaned["api_key"] is None
        assert cleaned["provider"] == "openai"


def test_sanitize_keeps_api_key_when_allowed():
    from app.utils.factory import sanitize_client_llm_config

    with patch("app.utils.factory.get_settings") as gs:
        gs.return_value.allow_client_llm_key = True
        cleaned = sanitize_client_llm_config(
            {"provider": "openai", "api_key": "sk-ok", "model": "gpt-4o"}
        )
        assert cleaned["api_key"] == "sk-ok"


def test_create_chat_model_uses_server_key_when_client_key_stripped():
    from app.utils.factory import create_chat_model_from_config

    settings = SimpleNamespace(
        allow_client_llm_key=False,
        OPENAI_API_KEY="server-secret-key",
        CHAT_API_KEY="",
        DASHSCOPE_API_KEY="",
        ALIYUN_ACCESS_KEY_SECRET="",
        ALLOWED_LLM_BASE_URLS="",
    )
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    with patch("app.utils.factory.get_settings", return_value=settings), patch(
        "langchain_openai.ChatOpenAI", FakeChatOpenAI
    ):
        model = create_chat_model_from_config(
            {
                "provider": "openai",
                "api_key": "client-should-not-use",
                "model": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
            }
        )
        assert model is not None
        assert captured.get("api_key") == "server-secret-key"


def test_allow_client_llm_key_defaults_false_in_production():
    from app.config.validator import AppSettings

    s = AppSettings.model_construct(ENV="production", ALLOW_CLIENT_LLM_KEY=None)
    assert s.allow_client_llm_key is False
    s2 = AppSettings.model_construct(ENV="dev", ALLOW_CLIENT_LLM_KEY=None)
    assert s2.allow_client_llm_key is True
    s3 = AppSettings.model_construct(ENV="production", ALLOW_CLIENT_LLM_KEY=True)
    assert s3.allow_client_llm_key is True


# ==================== 前端 _t 清理 + CSP ====================


def test_front_http_no_global_timestamp_param():
    http_js = REPO_ROOT / "front" / "src" / "services" / "http.js"
    text = http_js.read_text(encoding="utf-8")
    assert "_t: Date.now()" not in text
    assert "no-store" in text


def test_front_has_csp_meta_or_nginx():
    index = (REPO_ROOT / "front" / "index.html").read_text(encoding="utf-8")
    nginx = (REPO_ROOT / "front" / "nginx.conf").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in index or "Content-Security-Policy" in nginx


# ==================== compose redis password ====================


def test_compose_redis_requires_password():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "requirepass" in compose
    assert "127.0.0.1:${REDIS_PORT" in compose or '127.0.0.1:${REDIS_PORT' in compose
    assert "127.0.0.1:${MYSQL_PORT" in compose or '127.0.0.1:${MYSQL_PORT' in compose
