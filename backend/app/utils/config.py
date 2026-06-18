"""
配置加载 —— 从 YAML 文件读取 ChromaDB 和提示词配置。

配置文件：
- app/config/chroma.yaml: 向量数据库配置（集合名称、切片大小、分隔符等）
- app/config/prompt.yaml: 提示词模板路径配置
"""
import yaml
from app.utils.path_tool import get_abstract_path


def load_config(config_path: str, encoding: str = 'utf-8') -> dict:
    """加载 YAML 配置文件"""
    with open(config_path, 'r', encoding=encoding) as file:
        config = yaml.load(file, Loader=yaml.FullLoader)
    return config


# 全局配置字典
chroma_config = load_config(config_path=get_abstract_path('app/config/chroma.yaml'))
prompt_config = load_config(config_path=get_abstract_path('app/config/prompt.yaml'))