"""
路径工具 —— 提供项目根目录、数据目录、配置目录的绝对路径解析。

所有路径都基于项目根目录（backend/）进行解析，
确保在不同工作目录下运行时路径始终正确。
"""
import os


def get_project_root() -> str:
    """
    获取项目根目录（backend/）。

    通过当前文件位置向上两级推算：
    app/utils/path_tool.py -> app/utils -> app -> backend
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(app_dir)
    return project_root


def get_abstract_path(relative_path: str) -> str:
    """
    将相对路径转换为基于项目根目录的绝对路径。

    :param relative_path: 相对路径（如 'data/chromadb', 'app/config/chroma.yaml'）
    :return: 绝对路径
    """
    project_path = get_project_root()
    abstract_path = os.path.normpath(os.path.join(project_path, relative_path))
    return abstract_path


def get_data_path() -> str:
    """获取数据目录（backend/data/）的绝对路径"""
    return get_abstract_path('data')