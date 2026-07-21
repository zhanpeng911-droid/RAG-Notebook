"""
防护栏 —— 超时、循环、权限和注入防护。

职责：
- 总超时控制
- 检索轮次限制
- 输入清洗
- 权限验证
"""
import re
import time
from typing import Optional

from app.core.logger_handler import logger


class Guardrails:
    """Agent 防护栏"""

    # 配置
    MAX_TOTAL_TIME = 45  # 总超时（秒）
    MAX_RETRIEVAL_ROUNDS = 2  # 最大检索轮次
    MAX_QUERIES_PER_ROUND = 3  # 每轮最大查询数
    MAX_CONTEXT_CHUNKS = 15  # 最大上下文块数
    MAX_QUERY_LENGTH = 500  # 最大查询长度

    def __init__(self):
        self.start_time: Optional[float] = None

    def start(self):
        """开始计时"""
        self.start_time = time.time()

    def check_timeout(self) -> bool:
        """
        检查是否超时。

        :return: True 表示未超时，False 表示已超时
        """
        if self.start_time is None:
            return True
        elapsed = time.time() - self.start_time
        return elapsed < self.MAX_TOTAL_TIME

    def get_elapsed_time(self) -> float:
        """获取已用时间"""
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    def check_retrieval_rounds(self, current_round: int) -> bool:
        """
        检查检索轮次是否超限。

        :param current_round: 当前轮次
        :return: True 表示可以继续，False 表示已超限
        """
        return current_round < self.MAX_RETRIEVAL_ROUNDS

    def sanitize_query(self, query: str) -> str:
        """
        清洗查询文本。

        - 移除潜在的注入攻击
        - 限制长度
        - 去除首尾空白
        """
        if not query:
            return ""

        # 移除可能的 prompt injection
        dangerous_patterns = [
            r"ignore\s+previous\s+instructions",
            r"system\s*:",
            r"assistant\s*:",
            r"human\s*:",
            r"<\|system\|>",
            r"<\|assistant\|>",
            r"<\|human\|>",
        ]

        cleaned = query
        for pattern in dangerous_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

        # 限制长度
        if len(cleaned) > self.MAX_QUERY_LENGTH:
            cleaned = cleaned[:self.MAX_QUERY_LENGTH]

        return cleaned.strip()

    def validate_user_id(self, user_id: str) -> bool:
        """验证用户 ID"""
        if not user_id:
            return False
        # 简单格式验证
        return len(user_id) <= 64 and bool(re.match(r'^[a-zA-Z0-9_-]+$', user_id))

    def validate_space_id(self, space_id: str) -> bool:
        """验证空间 ID"""
        if not space_id:
            return True  # 空间 ID 可以为空
        return len(space_id) <= 64 and bool(re.match(r'^[a-zA-Z0-9_-]+$', space_id))


# 全局实例
guardrails = Guardrails()
