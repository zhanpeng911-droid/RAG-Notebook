"""
重排序服务 —— 基于 DashScope qwen3-vl-rerank 模型。

对混合检索的候选文档进行交叉编码重排序，提升 top-k 精度。
"""
import asyncio
import json
import urllib.request
from typing import List, Optional
from dataclasses import dataclass

from app.core.logger_handler import logger


@dataclass
class RerankResult:
    """重排序结果"""
    index: int          # 在原候选列表中的索引
    score: float        # 重排序分数
    text: str           # 文本内容


class Reranker:
    """
    重排序器 —— 调用 DashScope qwen3-vl-rerank。

    API: POST https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
    计费: ¥0.5 / 百万 input tokens（仅输入计费，极低成本）
    """

    def __init__(self, api_key: str = None, model: str = "qwen3-vl-rerank"):
        """
        :param api_key: DashScope API Key（默认从 settings 读取）
        :param model: 重排序模型名
        """
        self.model = model
        if api_key:
            self.api_key = api_key
        else:
            from app.config.validator import get_settings
            settings = get_settings()
            self.api_key = settings.DASHSCOPE_API_KEY or settings.ALIYUN_ACCESS_KEY_SECRET or ""

    async def rerank(
        self,
        query: str,
        documents: List[str],
        top_n: Optional[int] = None,
    ) -> List[RerankResult]:
        """
        对候选文档进行重排序。

        :param query: 查询文本
        :param documents: 候选文档文本列表
        :param top_n: 返回前 N 条（None = 全部）
        :return: 按分数降序的重排序结果
        """
        if not self.api_key:
            logger.warning("【重排序】DashScope API Key 未配置，跳过重排序")
            return []
        if not documents:
            return []

        payload = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "return_documents": False,
            },
        }
        if top_n is not None:
            payload["input"]["top_n"] = top_n

        req = urllib.request.Request(
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            resp = await asyncio.to_thread(
                urllib.request.urlopen, req, None, 30
            )
            body = json.loads(resp.read().decode("utf-8"))
            results = body.get("output", {}).get("results", [])

            return [
                RerankResult(
                    index=int(r.get("index", 0)),
                    score=float(r.get("relevance_score", 0.0)),
                    text=documents[int(r.get("index", 0))] if int(r.get("index", 0)) < len(documents) else "",
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"【重排序】调用失败: {e}")
            return []


# 全局单例
reranker = Reranker()
