"""
启动配置验证 —— 使用 pydantic-settings 定义配置模型，启动时校验关键配置。

校验项：
- SECRET_KEY: JWT 密钥不能为空
- MYSQL_HOST: MySQL 主机地址
- REDIS_HOST: Redis 主机地址
- LLM_TYPE: 必须是 ALIYUN/OLLAMA/OPENAI 之一

校验失败时打印清晰错误并退出，避免运行时报错。

使用方式：
    from app.config.validator import get_settings
    settings = get_settings()
    # settings.MYSQL_HOST, settings.REDIS_PORT, etc.
"""
import sys
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings
from pydantic import field_validator


ALLOWED_LLM_TYPES = {"ALIYUN", "OLLAMA", "OPENAI"}


class AppSettings(BaseSettings):
    """应用配置模型 —— 自动从 .env 文件读取"""

    # ==================== 环境配置 ====================
    ENV: str = "dev"
    DEBUG_MODE: bool = True

    # ==================== JWT 配置 ====================
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    JWT_BLACKLIST_CHECK_ENABLED: bool = True
    JWT_BLACKLIST_REDIS_URL: str = ""

    # ==================== MySQL 配置 ====================
    MYSQL_HOST: str = ""
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = ""
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "chat_history"

    # ==================== Redis 配置 ====================
    REDIS_HOST: str = ""
    REDIS_PORT: int = 6379
    REDIS_DB: int = 3
    REDIS_CACHE_URL: str = ""

    # ==================== Django 用户服务 ====================
    DJANGO_API_URL: str = "http://127.0.0.1:8001"

    # ==================== CORS ====================
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # ==================== 限流 ====================
    RATE_LIMIT_ENABLED: bool = True

    # ==================== LLM 配置 ====================
    LLM_TYPE: str = ""
    CHAT_API_KEY: str = ""
    CHAT_MODEL_NAME: str = "deepseek-chat"

    # OpenAI 兼容
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = ""

    # 阿里云
    ALIYUN_ACCESS_KEY_SECRET: str = ""
    ALIYUN_BASE_URL: str = ""
    ALIYUN_MODEL_NAME: str = ""
    DASHSCOPE_API_KEY: str = ""

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL_NAME: str = "qwen3:7b"
    OLLAMA_CHAT_MODEL_NAME: str = ""

    # URL 白名单
    ALLOWED_LLM_BASE_URLS: str = ""

    # ==================== Embedding 配置 ====================
    EMBED_MODEL_TYPE: str = ""
    TEXT_EMBEDDING_MODEL_NAME: str = "qwen3-embedding:0.6b"
    ALIYUN_EMBED_MODEL_NAME: str = "qwen3-embedding"

    # ==================== Vision 配置 ====================
    VISION_MODEL_TYPE: str = ""
    VISION_CHAT_MODEL_NAME: str = ""
    VISION_OLLAMA_MODEL_NAME: str = ""
    VISION_BATCH_SIZE: int = 5
    VISION_DEDUP_ENABLED: bool = True
    VISION_DEDUP_THRESHOLD: int = 10
    VISION_BATCH_LOW_RES: bool = True

    # ==================== LangChain 追踪 ====================
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = ""

    # ==================== 重排序 ====================
    RERANKER_MODEL_PATH: str = ""

    class Config:
        env_file = ".env"
        extra = "allow"

    @field_validator("LLM_TYPE")
    @classmethod
    def validate_llm_type(cls, v: str) -> str:
        if v and v not in ALLOWED_LLM_TYPES:
            raise ValueError(f"LLM_TYPE 必须是 {ALLOWED_LLM_TYPES} 之一，当前值: {v}")
        return v

    @property
    def mysql_url(self) -> str:
        """构建 MySQL 异步连接 URL"""
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            f"?charset=utf8mb4"
        )

    @property
    def jwt_blacklist_redis_url(self) -> str:
        """JWT 黑名单 Redis URL，优先使用专用配置，回退到通用 Redis URL"""
        return self.JWT_BLACKLIST_REDIS_URL or self.REDIS_CACHE_URL


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """获取全局配置单例（线程安全，首次调用时加载）"""
    return AppSettings()


def validate_startup_config() -> AppSettings:
    """
    启动时校验关键配置。

    校验失败时打印清晰错误并退出，避免运行时报错。
    """
    errors: List[str] = []

    try:
        settings = get_settings()
    except Exception as e:
        print(f"[CONFIG ERROR] 配置加载失败: {e}")
        sys.exit(1)

    if not settings.SECRET_KEY:
        errors.append("SECRET_KEY 不能为空，请在 .env 中配置 JWT 密钥")

    if not settings.MYSQL_HOST:
        errors.append("MYSQL_HOST 不能为空，请在 .env 中配置 MySQL 主机地址")

    if not settings.REDIS_HOST:
        errors.append("REDIS_HOST 不能为空，请在 .env 中配置 Redis 主机地址")

    if settings.LLM_TYPE and settings.LLM_TYPE not in ALLOWED_LLM_TYPES:
        errors.append(f"LLM_TYPE 必须是 {ALLOWED_LLM_TYPES} 之一，当前值: {settings.LLM_TYPE}")

    if errors:
        print("=" * 60)
        print("[CONFIG ERROR] 启动配置验证失败，请修复以下问题:")
        print("=" * 60)
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        print("=" * 60)
        print("请检查 .env 文件，参考 .env.example 模板")
        sys.exit(1)

    return settings
