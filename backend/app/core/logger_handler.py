"""
统一日志模块 —— 提供全局 logger 实例。

日志配置：
- 控制台输出：INFO 级别
- 文件输出：DEBUG 级别
- 日志目录：backend/logs/
- 文件命名：{name}_{日期}.log
"""
import logging
import os
from datetime import datetime

# 获取项目根目录
project_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

# 如果没有logs文件夹，则创建
logs_dir = os.path.join(project_path, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# 统一日志格式
class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from app.core.request_context import get_request_id
            record.request_id = get_request_id() or "-"
        except Exception:
            record.request_id = "-"
        return True


DEFAULT_LOGGING_FORMAT = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s rid=%(request_id)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_logger(
        name: str = "agent",
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
        log_file: str = None,
) -> logging.Logger:
    """
    获取或创建 logger 实例。

    :param name: logger 名称
    :param console_level: 控制台日志级别
    :param file_level: 文件日志级别
    :param log_file: 日志文件名（默认: {name}_{日期}.log）
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加处理器
    if logger.handlers:
        return logger

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(DEFAULT_LOGGING_FORMAT)
    console_handler.addFilter(RequestIdFilter())
    logger.addHandler(console_handler)

    # 文件处理器
    if log_file is None:
        log_file = f"{name}_{datetime.now().strftime('%Y%m%d')}.log"

    logs_dir = os.path.join(project_path, 'logs')
    os.makedirs(logs_dir, exist_ok=True)

    file_handler = logging.FileHandler(os.path.join(logs_dir, log_file), encoding='utf-8')
    file_handler.setLevel(file_level)
    file_handler.setFormatter(DEFAULT_LOGGING_FORMAT)
    file_handler.addFilter(RequestIdFilter())
    logger.addHandler(file_handler)

    return logger


# 全局 logger 实例 —— 所有模块统一使用这个 logger
logger = get_logger()