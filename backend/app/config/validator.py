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
from pydantic_settings import SettingsConfigDict
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
    REDIS_PASSWORD: str = ""
    REDIS_CACHE_URL: str = ""

    # ==================== Django 用户服务 ====================
    DJANGO_API_URL: str = "http://127.0.0.1:8001"

    # ==================== CORS ====================
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # ==================== 限流 ====================
    RATE_LIMIT_ENABLED: bool = True
    """限流总开关（bool）。个人开发环境可设 false 关闭。默认 true。"""

    # 是否允许前端传入 llm_config.api_key；生产默认 false
    ALLOW_CLIENT_LLM_KEY: bool | None = None
    """是否允许前端传明文 API Key。None 时按 ENV 自动判断（prod 禁止，dev 允许）。"""

    RUNTIME_CONFIG_ADMIN_USER_IDS: str = ""
    """允许修改进程级检索参数的用户 ID，逗号分隔；为空时禁止修改。"""

    FEATURE_ORG: bool = True
    """企业功能（组织/空间/审计）开关。true 时启用 /org、/space、/audit 路由。"""

    # Note background processing. These are not prerequisites for a successful save.
    NOTE_VECTOR_INDEX_ENABLED: bool = True
    """笔记向量索引开关。关闭时笔记保存不触发向量索引（节省 embedding 调用）。"""

    NOTE_AUTO_TAG_ENABLED: bool = True
    """笔记自动标签开关。关闭时笔记保存不触发 LLM 自动打标签。"""

    # ==================== LLM 配置 ====================
    LLM_TYPE: str = ""
    """LLM 提供商类型，可选值：OLLAMA / ALIYUN / OPENAI。必填。"""

    CHAT_API_KEY: str = ""
    """通用 Chat API Key，优先级低于各 provider 专用 key。"""

    CHAT_MODEL_NAME: str = "deepseek-chat"
    """默认对话模型名称。OPENAI 类型下用此字段指定模型（如 deepseek-v4-flash）。"""

    # OpenAI 兼容
    OPENAI_API_KEY: str = ""
    """OpenAI 兼容 API Key（DeepSeek 等）。LLM_TYPE=OPENAI 时必填。"""

    OPENAI_API_BASE: str = ""
    """OpenAI 兼容 API 地址（如 https://api.deepseek.com）。LLM_TYPE=OPENAI 时必填。"""

    # 阿里云
    ALIYUN_ACCESS_KEY_SECRET: str = ""
    ALIYUN_BASE_URL: str = ""
    ALIYUN_MODEL_NAME: str = ""
    DASHSCOPE_API_KEY: str = ""
    """阿里云 DashScope API Key。用于嵌入模型（text-embedding）和百炼对话模型。"""

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    """Ollama 服务地址。LLM_TYPE=OLLAMA 时使用。"""

    OLLAMA_MODEL_NAME: str = "qwen3:7b"
    """Ollama 默认模型名称。LLM_TYPE=OLLAMA 时使用。"""

    OLLAMA_CHAT_MODEL_NAME: str = ""
    """Ollama 对话模型名称（可与 OLLAMA_MODEL_NAME 不同）。为空时回退到 OLLAMA_MODEL_NAME。"""

    # URL 白名单
    ALLOWED_LLM_BASE_URLS: str = ""
    """LLM Base URL 白名单（逗号分隔）。为空时不校验；非空时只允许列出的地址。"""

    # ==================== Embedding 配置 ====================
    EMBED_MODEL_TYPE: str = ""
    """嵌入模型类型，可选值：OLLAMA / ALIYUN。控制向量嵌入使用哪个 provider。"""

    TEXT_EMBEDDING_MODEL_NAME: str = "qwen3-embedding:0.6b"
    """Ollama 嵌入模型名称。EMBED_MODEL_TYPE=OLLAMA 时使用。"""

    ALIYUN_EMBED_MODEL_NAME: str = "qwen3-embedding"
    """阿里云嵌入模型名称。EMBED_MODEL_TYPE=ALIYUN 时使用（如 qwen3.7-text-embedding）。"""

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

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="allow",
    )

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



    @property
    def allow_client_llm_key(self) -> bool:
        """生产环境默认禁止客户端明文 API Key；可用 ALLOW_CLIENT_LLM_KEY 显式覆盖。"""
        if self.ALLOW_CLIENT_LLM_KEY is not None:
            return bool(self.ALLOW_CLIENT_LLM_KEY)
        return self.ENV.lower() not in {"prod", "production"}

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() in {"prod", "production"}


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
