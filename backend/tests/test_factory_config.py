"""
LLM Factory 配置读取 + 安全校验测试。

覆盖风险点：
- _normalize_base_url 去除末尾 / 和空值处理
- _validate_llm_base_url 白名单/私网/用户名密码校验
- create_chat_model_from_config provider 默认 base_url
- factory.py 不再调用 load_dotenv
- factory.py 中 os.getenv 数量为 0
- agent.py 中 LLM 创建相关 os.getenv 已移除
"""
import pytest
from unittest.mock import patch


# ==================== _normalize_base_url ====================

def test_normalize_base_url_strips_trailing_slash():
    from app.utils.factory import _normalize_base_url
    assert _normalize_base_url("https://api.deepseek.com/") == "https://api.deepseek.com"


def test_normalize_base_url_strips_whitespace():
    from app.utils.factory import _normalize_base_url
    assert _normalize_base_url("  https://api.deepseek.com  ") == "https://api.deepseek.com"


def test_normalize_base_url_empty():
    from app.utils.factory import _normalize_base_url
    assert _normalize_base_url("") == ""
    assert _normalize_base_url(None) == ""


# ==================== _validate_llm_base_url ====================

def test_validate_deepseek_default_url():
    from app.utils.factory import _validate_llm_base_url
    result = _validate_llm_base_url("deepseek", "https://api.deepseek.com")
    assert result == "https://api.deepseek.com"


def test_validate_openai_default_url():
    from app.utils.factory import _validate_llm_base_url
    result = _validate_llm_base_url("openai", "https://api.openai.com/v1")
    assert result == "https://api.openai.com/v1"


def test_validate_ollama_localhost():
    from app.utils.factory import _validate_llm_base_url
    result = _validate_llm_base_url("ollama", "http://localhost:11434/v1")
    assert result == "http://localhost:11434/v1"


def test_validate_rejects_private_url_not_allowlisted():
    from app.utils.factory import _validate_llm_base_url
    with pytest.raises(ValueError):
        _validate_llm_base_url("custom", "http://192.168.1.100:8080/v1")


def test_validate_rejects_localhost_not_allowlisted():
    from app.utils.factory import _validate_llm_base_url
    with pytest.raises(ValueError):
        _validate_llm_base_url("custom", "http://localhost:9999/v1")


def test_validate_rejects_url_with_credentials():
    from app.utils.factory import _validate_llm_base_url
    with pytest.raises(ValueError, match="用户名或密码"):
        _validate_llm_base_url("custom", "https://user:pass@api.example.com/v1")


def test_validate_empty_url():
    from app.utils.factory import _validate_llm_base_url
    with pytest.raises(ValueError, match="不能为空"):
        _validate_llm_base_url("custom", "")


def test_validate_non_http_scheme():
    from app.utils.factory import _validate_llm_base_url
    with pytest.raises(ValueError, match="http"):
        _validate_llm_base_url("custom", "ftp://example.com/v1")


def test_validate_allowed_url_from_settings():
    from app.utils.factory import _validate_llm_base_url
    with patch("app.utils.factory.get_settings") as mock_settings:
        mock_settings.return_value.ALLOWED_LLM_BASE_URLS = "http://my-server:8080/v1"
        result = _validate_llm_base_url("custom", "http://my-server:8080/v1")
        assert result == "http://my-server:8080/v1"


# ==================== create_chat_model_from_config ====================

def test_create_chat_model_deepseek_default():
    """deepseek provider 应使用默认 base_url https://api.deepseek.com"""
    from app.utils.factory import create_chat_model_from_config
    # langchain_openai 已在 conftest.py 中 mock，ChatOpenAI 是 MagicMock
    # 直接调用并验证不抛异常（白名单校验通过）
    model = create_chat_model_from_config({
        "provider": "deepseek",
        "api_key": "test-key",
    })
    assert model is not None


def test_create_chat_model_custom_with_allowlisted_url():
    from app.utils.factory import create_chat_model_from_config
    with patch("app.utils.factory.get_settings") as mock_settings:
        mock_settings.return_value.ALLOWED_LLM_BASE_URLS = "http://my-server:8080/v1"
        model = create_chat_model_from_config({
            "provider": "custom",
            "base_url": "http://my-server:8080/v1",
            "api_key": "test-key",
            "model": "my-model",
        })
        assert model is not None


def test_create_chat_model_rejects_disallowed_custom_url():
    from app.utils.factory import create_chat_model_from_config
    with pytest.raises(ValueError):
        create_chat_model_from_config({
            "provider": "custom",
            "base_url": "http://10.0.0.1:8080/v1",
            "api_key": "test-key",
        })


# ==================== llm_config_is_usable ====================

def test_llm_config_is_usable_for_ollama_without_api_key():
    from app.utils.factory import llm_config_is_usable
    assert llm_config_is_usable({
        "provider": "ollama",
        "model": "qwen2.5",
        "base_url": "http://localhost:11434/v1",
    }) is True


def test_llm_config_is_not_usable_for_remote_provider_without_api_key():
    from app.utils.factory import llm_config_is_usable
    assert llm_config_is_usable({
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    }) is False


# ==================== factory.py 无 load_dotenv / os.getenv ====================

def test_factory_no_load_dotenv():
    """factory.py 不应再调用 load_dotenv"""
    import ast
    with open("app/utils/factory.py", "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            assert name != "load_dotenv", "factory.py 不应调用 load_dotenv"


def test_factory_no_os_getenv():
    """factory.py 中不应有 os.getenv 调用"""
    import ast
    with open("app/utils/factory.py", "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "getenv":
                # 检查是否是 os.getenv
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    pytest.fail("factory.py 不应调用 os.getenv")


# ==================== agent.py 无重复 LLM 创建 ====================

def test_agent_no_chat_tongyi_import():
    """agent.py 不应直接导入 ChatTongyi（已委托给 factory）"""
    with open("app/agent/agent.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "ChatTongyi" not in content, "agent.py 不应直接引用 ChatTongyi"


def test_agent_no_chat_ollama_import():
    """agent.py 不应直接导入 ChatOllama（已委托给 factory）"""
    with open("app/agent/agent.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "ChatOllama" not in content, "agent.py 不应直接引用 ChatOllama"


def test_agent_no_llm_type_getenv():
    """agent.py 中不应有 LLM_TYPE 的 os.getenv"""
    with open("app/agent/agent.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert 'getenv("LLM_TYPE"' not in content, "agent.py 不应读取 LLM_TYPE 环境变量"
