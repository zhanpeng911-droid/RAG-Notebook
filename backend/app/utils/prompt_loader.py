"""
提示词模板加载器 —— 从 YAML 配置中读取提示词模板文件路径，然后加载内容。

支持返回 version（见 prompt.yaml versions），便于日志与评测绑定。
"""
from functools import lru_cache
from typing import Tuple

from app.utils.config import prompt_config
from app.core.logger_handler import logger
from app.utils.path_tool import get_abstract_path


def _paths() -> dict:
    """兼容新旧 prompt.yaml：顶层即 path，或 paths 子树。"""
    if isinstance(prompt_config, dict) and "paths" in prompt_config:
        return prompt_config.get("paths") or {}
    return {k: v for k, v in (prompt_config or {}).items() if k != "versions" and isinstance(v, str)}


def _versions() -> dict:
    if isinstance(prompt_config, dict):
        return prompt_config.get("versions") or {}
    return {}


def get_prompt_version(prompt_type: str) -> str:
    return str(_versions().get(prompt_type, "0.0.0"))


@lru_cache(maxsize=32)
def load_prompt_with_version(prompt_type: str = "main_prompt") -> Tuple[str, str]:
    """
    加载提示词内容与版本号。

    :return: (content, version)
    """
    paths = _paths()
    if prompt_type not in paths:
        logger.error(f"【加载提示词模板】配置中不存在 {prompt_type} 类型的提示词")
        raise KeyError(f"配置中不存在 {prompt_type} 类型的提示词")

    prompt_path = get_abstract_path(paths[prompt_type])
    version = get_prompt_version(prompt_type)

    try:
        with open(prompt_path, encoding="utf-8") as f:
            content = f.read()
        logger.info(f"【加载提示词模板】type={prompt_type} version={version} path={prompt_path}")
        return content, version
    except Exception as e:
        logger.error(f"【加载提示词模板】读取 {prompt_path} 时出错: {e}")
        raise


def load_prompt(prompt_type: str = "main_prompt") -> str:
    """加载指定类型的提示词模板（仅内容）。"""
    content, _version = load_prompt_with_version(prompt_type)
    return content
