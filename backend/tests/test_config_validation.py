"""配置校验测试 —— 生产密钥约束 / MySQL URL 特殊字符 / 配置加载错误脱敏。

关键点：必须实际加载环境配置（setenv + cache_clear + 直接实例化），
不能只 monkeypatch 最终常量而漏掉根因。
"""
import pytest

import app.config.validator as validator
from app.config.validator import AppSettings, validate_startup_config


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    validator.get_settings.cache_clear()


def _require_db_env(monkeypatch):
    """隔离到 SECRET_KEY 校验：补齐 MySQL/Redis 必填项，避免环境差异影响断言。"""
    monkeypatch.setenv("MYSQL_HOST", "localhost")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("MYSQL_USER", "u")
    monkeypatch.setenv("MYSQL_PASSWORD", "p")


# ---------- MySQL URL：特殊字符密码 ----------

def test_mysql_url_special_chars_password_roundtrip(monkeypatch):
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "p@ss:/word%#1")
    monkeypatch.setenv("MYSQL_HOST", "db.internal")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_DATABASE", "chat_history")

    s = AppSettings()
    url = s.mysql_url

    # URL 对象持有原始密码，可直接用于引擎
    assert url.password == "p@ss:/word%#1"
    assert url.host == "db.internal"

    # 字符串渲染：保留真实密码（Alembic 需要），转义特殊字符，不掩码
    rendered = s.mysql_url_string
    assert "p@ss:/word%#1" not in rendered          # 原始密码不能以明文出现在 URL
    assert "***" not in rendered                     # 不能被掩码（迁移会失败）
    assert "p%40ss" in rendered                      # @ 已转义

    # str(URL) 会掩码密码，不能作为迁移 URL
    assert "***" in str(url)
    assert str(url) != rendered


def test_mysql_url_plain_password_roundtrip(monkeypatch):
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "normalpass")
    monkeypatch.setenv("MYSQL_HOST", "localhost")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_DATABASE", "db")

    s = AppSettings()
    assert s.mysql_url.password == "normalpass"
    assert "normalpass" in s.mysql_url_string


# ---------- 环境归一化 ----------

def test_env_normalized_strip_and_lower():
    assert AppSettings(ENV="  PRODUCTION  ").is_production is True
    assert AppSettings(ENV="prod").is_production is True
    assert AppSettings(ENV="production").is_production is True
    assert AppSettings(ENV="dev").is_production is False
    assert AppSettings(ENV=None).ENV == "dev"


# ---------- 生产密钥约束 ----------

def test_production_rejects_placeholder_secret(monkeypatch, capsys):
    _require_db_env(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "replace-me-before-shared-use")
    validator.get_settings.cache_clear()

    with pytest.raises(SystemExit):
        validate_startup_config()
    out = capsys.readouterr().out
    assert "SECRET_KEY" in out
    assert "占位符" in out


def test_production_rejects_short_secret(monkeypatch, capsys):
    _require_db_env(monkeypatch)
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", "short")
    validator.get_settings.cache_clear()

    with pytest.raises(SystemExit):
        validate_startup_config()
    out = capsys.readouterr().out
    assert "SECRET_KEY" in out
    assert "过短" in out


def test_dev_allows_short_or_placeholder_secret(monkeypatch):
    _require_db_env(monkeypatch)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("SECRET_KEY", "ci-test-secret")
    validator.get_settings.cache_clear()

    # dev 不应因占位/过短密钥退出
    validate_startup_config()


# ---------- 配置加载错误：不泄露输入值 ----------

def test_config_load_error_masks_input_values(monkeypatch, capsys):
    _require_db_env(monkeypatch)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("LLM_TYPE", "INVALID_PROVIDER")
    validator.get_settings.cache_clear()

    with pytest.raises(SystemExit):
        validate_startup_config()
    out = capsys.readouterr().out
    assert "LLM_TYPE" in out
    assert "INVALID_PROVIDER" not in out  # 不打印含输入值的完整配置异常
