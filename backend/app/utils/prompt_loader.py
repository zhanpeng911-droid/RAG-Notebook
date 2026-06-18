"""
提示词模板加载器 —— 从 YAML 配置中读取提示词模板文件路径，然后加载内容。

支持的提示词类型：
- main_prompt: Agent 主提示词
- rag_summary_prompt: RAG 摘要提示词
"""
from app.utils.config import prompt_config
from app.core.logger_handler import logger
from app.utils.path_tool import get_abstract_path


def load_prompt(prompt_type: str = 'main_prompt') -> str:
    """
    加载指定类型的提示词模板。

    :param prompt_type: 提示词类型，对应 prompt_config 中的键名
    :return: 提示词模板内容
    """
    if prompt_type not in prompt_config:
        logger.error(f"【加载提示词模板】配置中不存在 {prompt_type} 类型的提示词")
        raise KeyError(f"配置中不存在 {prompt_type} 类型的提示词")

    prompt_path = get_abstract_path(prompt_config[prompt_type])

    try:
        with open(prompt_path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"【加载提示词模板】读取 {prompt_path} 时出错: {e}")
        raise
