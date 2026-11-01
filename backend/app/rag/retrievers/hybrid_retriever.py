"""
混合检索器 —— 组合向量检索和 BM25 检索，提升召回质量。

动态权重调整策略：
- 长查询（>50字符）：向量权重 0.7，BM25 权重 0.3（语义理解更重要）
- 短查询（<20字符）：向量权重 0.3，BM25 权重 0.7（精确匹配更重要）
- 中等查询：各 0.5
"""
import asyncio
import hashlib
import json
import threading
from collections import OrderedDict
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever

from app.core.runtime_config import get as get_runtime_config

from .bm25_tokenizer import tokenize_for_bm25
from .empty_retriever import EmptyRetriever

# Cache index construction, never truncate the searchable corpus. Every lookup
# reads a fresh owner/space snapshot; changes from another process invalidate it
# by content, without relying on TTL or local write notifications.
BM25_CACHE_ENTRIES = 4
BM25_CACHE_PAYLOAD_BYTES = 32 * 1024 * 1024


class HybridRetriever:
    """混合检索器（BM25 + 向量检索）"""

    def __init__(self, vectors_store: Chroma):
        self.vectors_store = vectors_store
        self._bm25_cache = OrderedDict()
        self._bm25_cache_bytes = 0
        self._bm25_cache_lock = threading.Lock()

    def _build_bm25(self, user_id, space_id, snapshot, k):
        documents = []
        digest = hashlib.sha256()
        payload_bytes = 0
        metadatas = snapshot.get('metadatas') or []
        for i, content in enumerate(snapshot.get('documents') or []):
            metadata = metadatas[i] if i < len(metadatas) else {}
            metadata = metadata or {}
            documents.append(Document(page_content=content, metadata=metadata))
            encoded = json.dumps([content, metadata], sort_keys=True,
                                 ensure_ascii=False).encode('utf-8')
            digest.update(len(encoded).to_bytes(8, 'big'))
            digest.update(encoded)
            payload_bytes += len(encoded)
        scope = (user_id, space_id)
        fingerprint = digest.digest()
        with self._bm25_cache_lock:
            previous = self._bm25_cache.pop(scope, None)
            if previous is not None:
                self._bm25_cache_bytes -= previous[2]
            if not documents:
                return None
            if previous is not None and previous[0] == fingerprint:
                index = previous[1]
            else:
                index = BM25Retriever.from_documents(
                    documents=documents, k=k, preprocess_func=tokenize_for_bm25)
            if payload_bytes <= BM25_CACHE_PAYLOAD_BYTES:
                while self._bm25_cache and (
                    len(self._bm25_cache) >= BM25_CACHE_ENTRIES
                    or self._bm25_cache_bytes + payload_bytes > BM25_CACHE_PAYLOAD_BYTES
                ):
                    _, evicted = self._bm25_cache.popitem(last=False)
                    self._bm25_cache_bytes -= evicted[2]
                self._bm25_cache[scope] = (fingerprint, index, payload_bytes)
                self._bm25_cache_bytes += payload_bytes
            # A cached index is immutable per request: concurrent calls may ask
            # for different k. Authorization is still checked by the read path.
            return index.model_copy(update={'k': k})

    async def get_bm25_retriever(
        self, user_id: str = None, space_id: str = None, k: int | None = None
    ):
        """
        获取BM25检索器
        :param user_id: 用户ID，必须提供，否则返回None
        :return: BM25Retriever实例
        """
        if not user_id:
            return None

        where = {"user_id": user_id}
        if space_id is not None:
            where = {"$and": [{"user_id": user_id}, {"space_id": space_id}]}

        all_docs_result = await asyncio.to_thread(
            self.vectors_store.get,
            include=['documents', 'metadatas'],
            where=where,
        )
        return await asyncio.to_thread(
            self._build_bm25, user_id, space_id, all_docs_result,
            k if k is not None else get_runtime_config("retrieval.chroma_k"))

    async def _get_all_documents(self) -> list[Document]:
        """
        获取向量库中的所有文档
        :return: 文档列表
        """
        all_docs = await asyncio.to_thread(
            self.vectors_store.get,
            include=['documents', 'metadatas']
        )
        documents = []
        for i, doc in enumerate(all_docs['documents']):
            metadata = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
            documents.append(Document(page_content=doc, metadata=metadata))
        return documents

    async def get_retriever(
        self,
        query: str = None,
        user_id: str = None,
        space_id: str = None,
        candidate_k: int | None = None,
    ) -> BaseRetriever:
        """
        获取混合检索器（BM25 + 向量检索）
        :param query: 查询语句，用于动态调整权重
        :param user_id: 用户ID，用于过滤用户的文档，为空时不返回任何文档
        :return: EnsembleRetriever实例或单独的向量检索器
        """
        if not user_id:
            return EmptyRetriever()

        filter_dict = {'user_id': user_id}
        if space_id is not None:
            filter_dict = {'$and': [{'user_id': user_id}, {'space_id': space_id}]}
        retrieve_k = candidate_k or get_runtime_config("retrieval.chroma_k")
        vector_retriever = self.vectors_store.as_retriever(
            search_type='similarity',
            search_kwargs={
                'k': retrieve_k,
                'filter': filter_dict,
            },
        )
        bm25_retriever = await self.get_bm25_retriever(
            user_id, space_id=space_id, k=retrieve_k
        )

        if bm25_retriever:
            weights = await self.get_dynamic_weights(query)
            ensemble_retriever = EnsembleRetriever(
                retrievers=[vector_retriever, bm25_retriever],
                weights=weights
            )
            return ensemble_retriever
        else:
            return vector_retriever

    @staticmethod
    async def get_dynamic_weights(query: str = None):
        """
        根据查询动态调整权重
        :param query: 查询语句
        :return: 权重列表 [向量检索权重, BM25检索权重]
        """
        default_vector_weight = 0.5
        default_bm25_weight = 0.5

        if not query:
            return [default_vector_weight, default_bm25_weight]

        query_length = len(query)
        query_words = len(query.split())

        if query_length > 50:
            vector_weight = 0.7
            bm25_weight = 0.3
        elif query_length < 20:
            vector_weight = 0.3
            bm25_weight = 0.7
        else:
            vector_weight = default_vector_weight
            bm25_weight = default_bm25_weight

        if query_words > 0:
            word_density = query_words / query_length
            if word_density > 0.1:
                bm25_weight = min(bm25_weight + 0.1, 0.7)
                vector_weight = max(vector_weight - 0.1, 0.3)

        return [vector_weight, bm25_weight]
