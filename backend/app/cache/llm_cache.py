"""
LLM 响应缓存 —— 相同 prompt+model 组合的响应缓存到 Redis，减少重复调用。
"""
import hashlib
from app.core.logger_handler import logger


def _build_cache_key(prompt: str, model: str) -> str:
    """
    构造 LLM 响应缓存的 Redis 键名。

    键名格式为 "llm:{model}:{md5(prompt)}"，其中 prompt 的 MD5 哈希
    确保不同提示词对应不同的缓存条目，model 名称确保同一提示词在不同模型下
    有独立的缓存。

    Args:
        prompt: 发送给 LLM 的提示词文本。
        model: LLM 模型名称标识符（如 "qwen-vl-plus"）。

    Returns:
        缓存键名字符串，格式为 "llm:{model}:{32位md5哈希}"。
    """
    prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    return f"llm:{model}:{prompt_hash}"


async def get_cached_llm_response(prompt: str, model: str) -> str | None:
    """
    从 Redis 缓存中查询 LLM 的响应结果。

    使用 prompt 和 model 的组合作为缓存键，查找之前缓存的响应。
    如果 Redis 服务不可用或查询出错，静默返回 None（不会抛出异常），
    确保缓存故障不影响正常业务流程。

    Args:
        prompt: 发送给 LLM 的提示词文本。
        model: LLM 模型名称标识符。

    Returns:
        缓存的响应文本字符串，如果未命中缓存或 Redis 不可用则返回 None。
    """
    try:
        from app.db.redis_config import get_redis_cache_str
        key = _build_cache_key(prompt, model)
        cached = await get_redis_cache_str(key)
        if cached:
            logger.debug(f"LLM 缓存命中: {key}")
            return cached
        return None
    except Exception:
        return None


async def set_cached_llm_response(prompt: str, model: str, response: str, expire: int = 3600) -> bool:
    """
    将 LLM 的响应结果写入 Redis 缓存。

    缓存写入后会在指定时间后自动过期删除，避免缓存数据无限增长。
    如果 Redis 服务不可用或写入出错，静默返回 False（不会抛出异常）。

    Args:
        prompt: 发送给 LLM 的提示词文本（与 model 组合构成缓存键）。
        model: LLM 模型名称标识符。
        response: LLM 返回的响应文本，将被缓存。
        expire: 缓存过期时间（秒），默认 3600 秒（1小时）。

    Returns:
        True 表示缓存写入成功，False 表示写入失败或 Redis 不可用。
    """
    try:
        from app.db.redis_config import set_redis_cache
        key = _build_cache_key(prompt, model)
        success = await set_redis_cache(key, response, expire=expire)
        if success:
            logger.debug(f"LLM 缓存已写入: {key}")
        return success
    except Exception:
        return False
