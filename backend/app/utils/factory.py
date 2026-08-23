from abc import ABC, abstractmethod
from typing import Optional, List, Callable
import ipaddress
from urllib.parse import urlparse

from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
try:
    from langchain_ollama import OllamaEmbeddings, ChatOllama
except ImportError:
    OllamaEmbeddings = None
    ChatOllama = None

from app.core.logger_handler import logger
from app.config.validator import get_settings


class LazyModelProxy:
    """
    懒加载模型代理。

    目标：
    - 避免模块导入时立即实例化默认模型
    - 把依赖缺失或配置错误延后到首次真实使用时暴露
    - 保持现有 `chat_model` / `embed_model` / `vision_model` 导出名不变
    """

    def __init__(self, name: str, resolver: Callable[[], object]):
        self._name = name
        self._resolver = resolver
        self._resolved = None

    def resolve(self):
        """解析并缓存真实模型实例。"""
        if self._resolved is None:
            self._resolved = self._resolver()
        return self._resolved

    def __getattr__(self, item):
        return getattr(self.resolve(), item)

    def __repr__(self) -> str:
        state = "resolved" if self._resolved is not None else "pending"
        return f"<LazyModelProxy name={self._name!r} state={state}>"


class DashScopeEmbeddingsWrapper(Embeddings):
    """
    阿里云 DashScope 嵌入模型封装。

    适配 LangChain 的 Embeddings 接口，底层调用 DashScope TextEmbedding API。

    特性：
    - 批量嵌入：分批调用（每批最多 10 条）
    - 错误处理：API 调用失败时抛出异常
    """

    def __init__(self, model_name: str = "qwen3-embedding", api_key: str = None):
        """
        初始化 DashScope 嵌入模型。

        :param model_name: 模型名称（默认 qwen3-embedding）
        :param api_key: API Key（优先使用，否则从 settings 读取）
        """
        settings = get_settings()
        try:
            import dashscope
            self.dashscope = dashscope
            resolved_key = api_key or settings.DASHSCOPE_API_KEY or settings.ALIYUN_ACCESS_KEY_SECRET
            if not resolved_key:
                logger.error("DashScope API Key 未配置！请设置 DASHSCOPE_API_KEY 环境变量")
            self.dashscope.api_key = resolved_key
            self.model_name = model_name
        except ImportError:
            raise ImportError("需要安装 dashscope 库: pip install dashscope")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量嵌入文档 —— 分批调用 DashScope API。

        DashScope 限制每次最多 10 条，所以自动分批处理。

        :param texts: 文档文本列表
        :return: 嵌入向量列表
        """
        all_embeddings = []
        batch_size = 10
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self.dashscope.TextEmbedding.call(
                model=self.model_name,
                input=batch
            )
            if resp.status_code == 200:
                for item in resp.output['embeddings']:
                    all_embeddings.append(item['embedding'])
            else:
                logger.error(f"阿里云嵌入调用失败: {resp.message}")
                raise RuntimeError(f"DashScope embedding failed: {resp.message}")
        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        """
        嵌入单个查询 —— 用于向量检索。

        :param text: 查询文本
        :return: 嵌入向量
        """
        resp = self.dashscope.TextEmbedding.call(
            model=self.model_name,
            input=[text]
        )
        if resp.status_code == 200:
            return resp.output['embeddings'][0]['embedding']
        else:
            logger.error(f"阿里云嵌入调用失败: {resp.message}")
            raise RuntimeError(f"DashScope embedding failed: {resp.message}")


class BaseModelFactory(ABC):
    """模型工厂基类 —— 定义统一的模型创建接口"""

    @abstractmethod
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        """生成模型实例（由子类实现）"""
        pass


class ChatModelFactory(BaseModelFactory):
    """
    聊天模型工厂 —— 根据 LLM_TYPE 创建对应的聊天模型。

    支持的 LLM 后端：
    - OLLAMA: 本地 Ollama 模型
    - ALIYUN: 阿里云百炼（DashScope）
    - OPENAI: OpenAI 兼容接口
    """

    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        """根据 settings.LLM_TYPE 创建聊天模型实例"""
        return create_chat_model_from_settings()


class EmbedModelFactory(BaseModelFactory):
    """
    嵌入模型工厂 —— 根据 EMBED_MODEL_TYPE 创建嵌入模型。

    用途：
    - 文档向量化（存储到 ChromaDB）
    - 查询向量化（用于语义检索）

    支持的嵌入后端：
    - OLLAMA: 本地 Ollama 嵌入模型
    - ALIYUN: 阿里云 DashScope 嵌入模型
    """

    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        """根据 settings 创建嵌入模型实例"""
        settings = get_settings()
        embed_type = settings.EMBED_MODEL_TYPE.upper()

        if embed_type == "OLLAMA":
            if OllamaEmbeddings is None:
                raise ImportError("需要安装 langchain-ollama: pip install langchain-ollama")
            model_name = settings.TEXT_EMBEDDING_MODEL_NAME
            base_url = settings.OLLAMA_BASE_URL

            logger.info(f"📦 EmbedModel 使用Ollama嵌入模型: {model_name}, 地址: {base_url}")

            return OllamaEmbeddings(
                model=model_name,
                base_url=base_url
            )

        elif embed_type == "ALIYUN":
            model_name = settings.ALIYUN_EMBED_MODEL_NAME
            api_key = settings.DASHSCOPE_API_KEY or settings.ALIYUN_ACCESS_KEY_SECRET

            logger.info(f"📦 EmbedModel 使用阿里云嵌入模型: {model_name}")

            return DashScopeEmbeddingsWrapper(
                model_name=model_name,
                api_key=api_key
            )

        else:
            raise ValueError(f"不支持的EMBED_MODEL_TYPE: {embed_type}，可选值: OLLAMA, ALIYUN")


class VisionModelFactory(BaseModelFactory):
    """
    视觉模型工厂 —— 用于 PDF 多模态加载场景。

    用途：
    - 将 PDF 页面渲染为图片
    - 调用视觉模型理解图片内容
    - 提取图表、表格、流程图等视觉信息

    为什么单独一个工厂：
    1. 视觉模型不用 streaming（图片理解不适合流式）
    2. 可能有独立的模型配置（VISION_OLLAMA_MODEL_NAME）
    3. 可能使用更大的多模态模型（如 qwen-vl 系列）
    """

    def generator(self) -> Optional[BaseChatModel]:
        """根据 settings 创建视觉模型实例"""
        settings = get_settings()
        # 未设置 VISION_MODEL_TYPE 时，默认跟随 LLM_TYPE（保持向后兼容）
        vision_type = settings.VISION_MODEL_TYPE.upper() or settings.LLM_TYPE.upper()

        if vision_type == "OLLAMA":
            if ChatOllama is None:
                raise ImportError("需要安装 langchain-ollama: pip install langchain-ollama")
            model_name = settings.VISION_OLLAMA_MODEL_NAME or settings.OLLAMA_MODEL_NAME or "qwen-vl:7b"
            base_url = settings.OLLAMA_BASE_URL

            logger.info(f"🎨 VisionModel 使用Ollama多模态模型: {model_name}, 地址: {base_url}")

            return ChatOllama(
                model=model_name,
                base_url=base_url,
                # 视觉模型禁用 streaming，因为图片理解需要在完整的上下文上做推理
                streaming=False,
                top_p=0.7,
            )

        elif vision_type == "ALIYUN":
            model_name = settings.VISION_CHAT_MODEL_NAME or settings.CHAT_MODEL_NAME or "qwen3-max"
            api_key = settings.ALIYUN_ACCESS_KEY_SECRET
            base_url = settings.ALIYUN_BASE_URL

            logger.info(f"🎨 VisionModel 使用阿里云百炼多模态模型: {model_name}")

            return ChatTongyi(
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                streaming=False,
                top_p=0.7,
            )

        elif vision_type == "OPENAI":
            from langchain_openai import ChatOpenAI
            model_name = settings.VISION_CHAT_MODEL_NAME or settings.CHAT_MODEL_NAME or "deepseek-chat"
            api_key = settings.OPENAI_API_KEY
            base_url = settings.OPENAI_API_BASE

            logger.info(f"🎨 VisionModel 使用 OpenAI 兼容模型: {model_name}")

            return ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                streaming=False,
                temperature=0.7,
            )

        else:
            raise ValueError(f"不支持的VISION_MODEL_TYPE: {vision_type}，可选值: ALIYUN, OLLAMA, OPENAI")


# ==================== 核心创建函数 ====================


def create_chat_model_from_settings(custom_model: Optional[str] = None) -> BaseChatModel:
    """
    根据 settings.LLM_TYPE 创建默认聊天模型。

    被 ChatModelFactory.generator() 和 AgentFactory._create_chat_model() 共用，
    消除 agent.py 中的重复 LLM 创建逻辑。

    :param custom_model: 自定义模型名称（覆盖 settings 中的默认值）
    :return: LangChain 兼容的聊天模型实例
    """
    settings = get_settings()
    llm_type = settings.LLM_TYPE.upper()

    if llm_type == "OLLAMA":
        if ChatOllama is None:
            raise ImportError("需要安装 langchain-ollama: pip install langchain-ollama")
        model_name = custom_model or settings.OLLAMA_MODEL_NAME or settings.OLLAMA_CHAT_MODEL_NAME or "qwen3:7b"
        base_url = settings.OLLAMA_BASE_URL

        logger.info(f"📦 ChatModel 使用Ollama模型: {model_name}, 地址: {base_url}")

        return ChatOllama(
            model=model_name,
            base_url=base_url,
            streaming=True,
            top_p=0.7,
        )

    elif llm_type == "ALIYUN":
        model_name = custom_model or settings.ALIYUN_MODEL_NAME or settings.CHAT_MODEL_NAME or "qwen3-max"
        api_key = settings.DASHSCOPE_API_KEY or settings.ALIYUN_ACCESS_KEY_SECRET
        base_url = settings.ALIYUN_BASE_URL

        logger.info(f"📦 ChatModel 使用阿里云百炼模型: {model_name}")

        return ChatTongyi(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            streaming=True,
            top_p=0.7,
        )

    elif llm_type == "OPENAI":
        from langchain_openai import ChatOpenAI
        model_name = custom_model or settings.CHAT_MODEL_NAME or "deepseek-chat"
        api_key = settings.OPENAI_API_KEY
        base_url = settings.OPENAI_API_BASE

        logger.info(f"📦 ChatModel 使用 OpenAI 兼容模型: {model_name}, base_url: {base_url}")
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            streaming=True,
            temperature=0.7,
        )

    else:
        raise ValueError(f"不支持的LLM_TYPE: {llm_type}，可选值: ALIYUN, OLLAMA, OPENAI")


def get_default_chat_model() -> BaseChatModel:
    """获取默认聊天模型实例（懒加载）。"""
    return chat_model.resolve()


def get_default_embed_model() -> Embeddings:
    """获取默认嵌入模型实例（懒加载）。"""
    return embed_model.resolve()


def get_default_vision_model() -> BaseChatModel:
    """获取默认视觉模型实例（懒加载）。"""
    return vision_model.resolve()


# 模块级默认模型代理 —— 首次使用时才真正初始化模型
chat_model = LazyModelProxy("chat_model", lambda: ChatModelFactory().generator())
embed_model = LazyModelProxy("embed_model", lambda: EmbedModelFactory().generator())
vision_model = LazyModelProxy("vision_model", lambda: VisionModelFactory().generator())


# ==================== base_url 安全校验 ====================


def _normalize_base_url(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _hostname_is_private(hostname: str | None) -> bool:
    if not hostname:
        return True
    normalized = hostname.lower().strip("[]")
    if normalized in {"localhost", "0.0.0.0"}:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


def _allowed_llm_base_urls() -> set[str]:
    settings = get_settings()
    defaults = {
        "https://api.deepseek.com",
        "https://api.openai.com/v1",
        "https://api.anthropic.com",
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
    }
    configured = {
        _normalize_base_url(url)
        for url in settings.ALLOWED_LLM_BASE_URLS.split(",")
        if url.strip()
    }
    return defaults | configured


def _validate_llm_base_url(provider: str, base_url: str) -> str:
    """Validate dynamic LLM endpoints before the backend makes outbound requests."""
    normalized_url = _normalize_base_url(base_url)
    if not normalized_url:
        raise ValueError("LLM base_url 不能为空")

    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LLM base_url 必须是完整的 http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("LLM base_url 不允许包含用户名或密码")

    allowed_urls = _allowed_llm_base_urls()
    is_allowlisted = any(
        normalized_url == allowed or normalized_url.startswith(f"{allowed}/")
        for allowed in allowed_urls
    )
    if is_allowlisted:
        return normalized_url

    if _hostname_is_private(parsed.hostname):
        raise ValueError("LLM base_url 不允许指向 localhost、私网或链路本地地址")

    raise ValueError("LLM base_url 不在 ALLOWED_LLM_BASE_URLS 白名单中")



def allow_client_llm_key() -> bool:
    """是否允许使用前端传入的 api_key。"""
    return get_settings().allow_client_llm_key


def sanitize_client_llm_config(config: dict | None) -> dict | None:
    """
    规范化前端 llm_config。
    生产（或 ALLOW_CLIENT_LLM_KEY=false）时剥离明文 api_key，强制走服务端密钥。
    """
    if not config:
        return None
    cleaned = dict(config)
    if not allow_client_llm_key():
        cleaned["api_key"] = None
    return cleaned


def _server_api_key_for_provider(provider: str) -> str:
    """按 provider 回退到服务端配置的密钥。"""
    settings = get_settings()
    provider = (provider or "").lower()
    if provider in {"deepseek", "openai", "custom"}:
        return (settings.OPENAI_API_KEY or settings.CHAT_API_KEY or "").strip()
    if provider in {"aliyun", "dashscope", "tongyi"}:
        return (settings.DASHSCOPE_API_KEY or settings.ALIYUN_ACCESS_KEY_SECRET or "").strip()
    if provider == "anthropic":
        return (settings.OPENAI_API_KEY or settings.CHAT_API_KEY or "").strip()
    return ""


def llm_config_is_usable(config: dict | None) -> bool:
    """Return whether a front-end supplied LLM config should override backend defaults."""
    if not config:
        return False
    provider = (config.get("provider") or "deepseek").lower()
    api_key = (config.get("api_key") or "").strip()

    # 生产：不依赖客户端 key；ollama 始终可用，其它 provider 需服务端有 key 或仅做模型选择回退
    if not allow_client_llm_key():
        if provider == "ollama":
            return True
        # 允许只传 provider/model，create 时回退服务端 key
        return True

    if provider == "ollama":
        return True
    return bool(api_key)




def create_chat_model_from_config(config: dict) -> BaseChatModel:
    """
    动态创建聊天模型 —— 根据前端传入的配置。

    支持的 provider：
    - deepseek: DeepSeek API
    - openai: OpenAI API
    - ollama: 本地 Ollama
    - custom: 自定义 OpenAI 兼容接口

    支持的 protocol：
    - openai: OpenAI 兼容协议（大多数 LLM 服务）
    - anthropic: Anthropic 协议（Claude）

    安全：
    - 生产环境（ALLOW_CLIENT_LLM_KEY=false）会剥离客户端 api_key，回退服务端密钥

    :param config: 配置字典
        - provider: LLM 提供商
        - model: 模型名称
        - api_key: API 密钥
        - base_url: API 地址
        - protocol: 协议类型
    :return: LangChain 兼容的聊天模型实例
    """
    config = sanitize_client_llm_config(config) or {}
    provider = (config.get("provider") or "deepseek").lower()
    protocol = (config.get("protocol") or "openai").lower()
    model_name = config.get("model")
    api_key = (config.get("api_key") or "").strip() or _server_api_key_for_provider(provider)
    base_url = config.get("base_url")

    # provider → 默认 base_url 映射
    PROVIDER_DEFAULTS = {
        "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
        "openai":   {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
        "ollama":   {"base_url": "http://localhost:11434/v1", "model": "qwen3:7b"},
        "anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-20250514"},
    }

    defaults = PROVIDER_DEFAULTS.get(provider, {})
    base_url = base_url or defaults.get("base_url", "")
    # custom provider 必须前端提供 model 和 base_url，否则报错
    if provider == "custom":
        if not model_name:
            raise ValueError("自定义 provider 必须指定 model 名称")
        if not base_url:
            raise ValueError("自定义 provider 必须指定 base_url")
    else:
        model_name = model_name or defaults.get("model", "deepseek-v4-flash")
    base_url = _validate_llm_base_url(provider, base_url)

    logger.info(f"📦 动态创建 ChatModel: provider={provider}, model={model_name}, base_url={base_url}")

    if protocol == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model=model_name,
                api_key=api_key,
                anthropic_api_url=base_url or "https://api.anthropic.com",
                streaming=True,
                temperature=0.7,
            )
        except ImportError:
            raise ImportError("需要安装 langchain-anthropic: pip install langchain-anthropic")

    # 所有 OpenAI 兼容协议（deepseek / openai / ollama / custom）
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name,
        api_key=api_key or "no-key",
        base_url=base_url,
        streaming=True,
        temperature=0.7,
    )
