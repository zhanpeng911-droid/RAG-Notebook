"""BM25 中文分词器 —— 供生产 HybridRetriever 与 IR 评测 runner 共用，保证同构。

独立成模块的原因：不依赖 langchain，可在 CI（langchain 被 mock）环境直接单测。
"""
import jieba


def tokenize_for_bm25(text: str) -> list[str]:
    """中英文混合分词（jieba + 小写化），供 BM25 检索使用。

    BM25Retriever 默认按空格分词，中文查询/文档无法切出 token，
    会导致 BM25 一路在中文场景下近乎失效（IR 评测已验证：recall 仅 0.60）。
    """
    return [t for t in jieba.lcut((text or "").lower()) if t.strip()]
