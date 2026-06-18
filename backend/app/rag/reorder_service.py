"""
重排序服务 —— 使用 DashScope API 对检索结果进行精排。

原理：
1. 向量检索/BM25 检索返回 top-k 候选文档
2. 重排序模型对每个 (query, document) 对进行精确打分
3. 按分数重新排序，返回最相关的文档

DashScope 重排序模型：
- gte-rerank：阿里云通义千问重排序模型
- 支持中文，精度高
"""
import os
from app.core.logger_handler import logger


class ReorderService:
    """
    重排序服务 —— 使用 DashScope TextRerank API。

    设计模式：双重检查锁定单例
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if ReorderService._initialized:
            return

        # 延迟加载 API Key，在调用时获取最新的环境变量
        ReorderService._initialized = True

    async def rerank(self, query: str, documents: list, top_k: int = 5) -> list:
        """
        重排序 —— 对检索结果进行精排。

        :param query: 用户查询
        :param documents: 候选文档列表（LangChain Document 对象）
        :param top_k: 返回前 top_k 个文档
        :return: 重排序后的文档列表
        """
        if not documents:
            logger.info("【重排序】无候选文档，跳过重排序")
            return []

        # 每次调用时获取最新的 API Key
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("【重排序】未配置 API Key，返回原始顺序")
            return documents[:top_k]

        try:
            from dashscope import TextReRank

            # 准备文档内容
            docs_text = [doc.page_content for doc in documents]

            logger.info(f"【重排序】开始精排，共 {len(documents)} 个候选文档，返回 top_{top_k}")

            # 设置 API Key
            import dashscope
            dashscope.api_key = api_key

            # 调用 DashScope 重排序 API
            response = TextReRank.call(
                model="gte-rerank",
                query=query,
                documents=docs_text,
                top_k=top_k,
                return_documents=True
            )

            # 解析结果
            if response.status_code == 200:
                reranked_docs = []
                for result in response.output.results:
                    idx = result.index
                    score = result.relevance_score
                    doc = documents[idx]
                    # 将重排序分数添加到 metadata
                    doc.metadata['rerank_score'] = float(score)
                    reranked_docs.append(doc)

                logger.info(f"【重排序】完成，返回 {len(reranked_docs)} 个文档，"
                           f"最高分: {reranked_docs[0].metadata.get('rerank_score', 0):.4f}")
                return reranked_docs
            else:
                logger.error(f"【重排序】API 调用失败: {response.code} - {response.message}")
                return documents[:top_k]

        except ImportError:
            logger.error("【重排序】dashscope 未安装，请运行: pip install dashscope")
            return documents[:top_k]
        except Exception as e:
            logger.error(f"【重排序】异常: {e}")
            return documents[:top_k]


# 全局单例
reorder_service = ReorderService()
