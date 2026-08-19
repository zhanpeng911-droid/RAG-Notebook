"""
统一检索层 —— 提供知识库、笔记和空间文档的统一检索接口。

M1 阶段核心组件：
- 统一证据结构（Evidence）
- 强制用户与空间隔离
- 文档块去重、相邻片段合并
- 向量不可用时降级为关键词检索
- 为后续重排序预留接口
"""
import asyncio
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Literal
from enum import Enum

from langchain_core.documents import Document

from app.core.logger_handler import logger


class SourceType(str, Enum):
    """证据来源类型"""
    KNOWLEDGE = "knowledge"
    NOTE = "note"


@dataclass
class Evidence:
    """
    统一证据结构 —— 所有检索结果的标准化表示。

    属性:
        source_type: 来源类型（knowledge 或 note）
        source_id: 文档或笔记 ID
        chunk_id: 切片 ID
        title: 文件名或笔记标题
        content: 正文片段
        score: 相关性分数
        metadata: 元数据（user_id, space_id 等）
    """
    source_type: str
    source_id: str
    chunk_id: str
    title: str
    content: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class RetrievalService:
    """
    统一检索服务 —— 协调知识库、笔记和空间文档的检索。

    使用方式:
        service = RetrievalService(user_id="user123")
        evidences = await service.retrieve("什么是机器学习？", scope="all")

    scope 参数:
        - "knowledge": 只检索知识库
        - "notes": 只检索笔记
        - "all": 检索知识库 + 笔记（默认）
        - "space:{space_id}": 检索指定空间的文档
    """

    def __init__(self, user_id: str, space_id: str = None, llm_config: dict = None):
        """
        初始化检索服务。

        :param user_id: 用户 ID（必须，用于数据隔离）
        :param space_id: 空间 ID（可选，用于空间级隔离）
        :param llm_config: 前端传入的 LLM 配置（可选，用于 HyDE 生成）
        """
        if not user_id:
            raise ValueError("user_id 不能为空")

        self.user_id = user_id
        self.space_id = space_id
        self.llm_config = llm_config

    async def retrieve(
        self,
        query: str,
        scope: str = "all",
        top_k: int = 8,
        use_hyde: bool = True,
        use_rerank: bool = True,
    ) -> List[Evidence]:
        """
        统一检索入口。

        :param query: 查询文本
        :param scope: 检索范围（knowledge | notes | all | space:{space_id}）
        :param top_k: 返回的最大结果数
        :param use_hyde: 是否使用 HyDE 生成假设性文档
        :param use_rerank: 是否使用重排序
        :return: 证据列表
        """
        if not query or not query.strip():
            return []

        logger.info(f"【统一检索】query={query[:50]}, scope={scope}, top_k={top_k}, use_rerank={use_rerank}")

        # 解析 scope
        effective_space_id = self.space_id
        if scope.startswith("space:"):
            effective_space_id = scope.split(":", 1)[1]

        # 重排序时扩大候选集（候选 top_k*3，重排后取 top_k）
        candidate_k = top_k * 3 if use_rerank else top_k

        # 并行检索知识库和笔记
        tasks = []

        if scope in ("knowledge", "all") or scope.startswith("space:"):
            tasks.append(self._retrieve_knowledge(query, candidate_k, effective_space_id, use_hyde))

        if scope in ("notes", "all") and not effective_space_id:
            tasks.append(self._retrieve_notes(query, candidate_k))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 合并结果
        all_evidences: List[Evidence] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"【统一检索】检索异常: {result}")
                continue
            all_evidences.extend(result)

        # 去重
        all_evidences = self._deduplicate(all_evidences)

        # 相邻片段合并
        all_evidences = self._merge_adjacent(all_evidences)

        # 重排序（如果启用且候选数足够）
        if use_rerank and len(all_evidences) > top_k:
            reranked = await self._rerank_evidences(query, all_evidences)
            if reranked:
                all_evidences = reranked

        # 排序
        all_evidences.sort(key=lambda e: e.score, reverse=True)

        # 截断
        all_evidences = all_evidences[:top_k]

        logger.info(f"【统一检索】返回 {len(all_evidences)} 个证据")
        return all_evidences

    async def _rerank_evidences(
        self, query: str, evidences: List[Evidence]
    ) -> List[Evidence]:
        """
        使用 qwen3-vl-rerank 对候选证据重排序。

        :param query: 原始查询
        :param evidences: 候选证据列表
        :return: 重排后的证据列表（按重排分数降序）
        """
        try:
            from app.rag.reranker import reranker

            texts = [e.content[:1000] for e in evidences]  # 限制单条长度
            results = await reranker.rerank(query=query, documents=texts)

            if not results:
                return []

            # 按重排分数重写 evidence 的 score
            reranked = []
            for r in results:
                if r.index < len(evidences):
                    ev = evidences[r.index]
                    ev.score = r.score
                    ev.metadata["rerank_score"] = r.score
                    reranked.append(ev)

            logger.info(f"【重排序】{len(evidences)} 条候选 → 重排完成")
            return reranked
        except Exception as e:
            logger.error(f"【重排序】失败: {e}")
            return []

    async def _retrieve_knowledge(
        self, query: str, top_k: int, space_id: str = None, use_hyde: bool = True
    ) -> List[Evidence]:
        """
        从知识库检索。

        流程：
        1. 尝试向量检索（如果 embedding 可用）
        2. 向量不可用时降级为关键词检索
        """
        try:
            from app.rag.vector_store import VectorStoreService
            store = VectorStoreService()

            # 检查 embedding 是否可用
            embedding_available = self._check_embedding()
            if not embedding_available:
                logger.warning("【统一检索】embedding 不可用，降级为关键词检索")
                return await self._keyword_search_knowledge(query, top_k, space_id)

            # 构建过滤条件
            filter_dict = {"user_id": self.user_id}
            if space_id:
                filter_dict["space_id"] = space_id

            # 获取检索器
            retriever = await store.get_retriever(query, self.user_id)

            # 使用 HyDE 生成假设性文档
            search_query = query
            if use_hyde:
                search_query = await self._generate_hyde(query)

            # 检索
            documents = await retriever.ainvoke(search_query)

            # 过滤空间
            if space_id:
                documents = [
                    doc for doc in documents
                    if doc.metadata.get("space_id", "") == space_id
                ]

            # 转换为 Evidence
            evidences = []
            for i, doc in enumerate(documents[:top_k]):
                evidence = Evidence(
                    source_type=SourceType.KNOWLEDGE,
                    source_id=doc.metadata.get("md5", ""),
                    chunk_id=doc.metadata.get("chunk_id", f"kb_{i}"),
                    title=doc.metadata.get("original_filename", "未知文件"),
                    content=doc.page_content,
                    score=doc.metadata.get("rerank_score", 0.8 - i * 0.05),
                    metadata={
                        "user_id": doc.metadata.get("user_id"),
                        "space_id": doc.metadata.get("space_id", ""),
                        "md5": doc.metadata.get("md5", ""),
                    }
                )
                evidences.append(evidence)

            return evidences

        except Exception as e:
            logger.error(f"【统一检索】知识库检索失败: {e}")
            return await self._keyword_search_knowledge(query, top_k, space_id)

    async def _retrieve_notes(self, query: str, top_k: int) -> List[Evidence]:
        """从笔记库检索"""
        try:
            from app.services.note_service import note_service

            # 检查 embedding 是否可用
            embedding_available = self._check_embedding()
            if not embedding_available:
                logger.warning("【统一检索】embedding 不可用，降级为关键词检索笔记")
                return await self._keyword_search_notes(query, top_k)

            # 向量检索笔记
            note_docs = await asyncio.to_thread(
                note_service.notes_store.similarity_search,
                query, k=top_k,
                filter={"user_id": self.user_id}
            )

            # 转换为 Evidence
            evidences = []
            for i, doc in enumerate(note_docs):
                evidence = Evidence(
                    source_type=SourceType.NOTE,
                    source_id=doc.metadata.get("note_id", ""),
                    chunk_id=f"note_{i}",
                    title=doc.metadata.get("title", "无标题笔记"),
                    content=doc.page_content,
                    score=0.8 - i * 0.05,
                    metadata={
                        "user_id": doc.metadata.get("user_id"),
                        "note_id": doc.metadata.get("note_id"),
                    }
                )
                evidences.append(evidence)

            return evidences

        except Exception as e:
            logger.error(f"【统一检索】笔记检索失败: {e}")
            return await self._keyword_search_notes(query, top_k)

    async def _keyword_search_knowledge(
        self, query: str, top_k: int, space_id: str = None
    ) -> List[Evidence]:
        """
        关键词检索知识库（降级方案）。

        当 embedding 不可用时，使用 MySQL LIKE 查询搜索文档标题和内容。
        """
        try:
            from app.db.db_config import AsyncSessionLocal
            from app.repositories.document_index_repository import DocumentIndexRepository
            from app.models.document_index import DocumentIndexStatus

            async with AsyncSessionLocal() as session:
                repo = DocumentIndexRepository(session)
                docs = await repo.get_user_documents(
                    self.user_id, space_id=space_id, status=DocumentIndexStatus.INDEXED
                )

                # 简单关键词匹配（标题和内容）
                keywords = query.lower().split()
                matched = []

                for doc in docs:
                    title = doc.original_filename.lower()
                    # 标题匹配
                    title_match = any(kw in title for kw in keywords)
                    if title_match:
                        matched.append(Evidence(
                            source_type=SourceType.KNOWLEDGE,
                            source_id=doc.md5,
                            chunk_id=f"kb_{doc.id}",
                            title=doc.original_filename,
                            content=f"[文件: {doc.original_filename}]",
                            score=0.5,
                            metadata={
                                "user_id": doc.user_id,
                                "space_id": doc.space_id or "",
                                "md5": doc.md5,
                            }
                        ))

                return matched[:top_k]

        except Exception as e:
            logger.error(f"【统一检索】关键词检索知识库失败: {e}")
            return []

    @staticmethod
    def _build_note_keyword_filter(note_model, user_id: str, query: str):
        """Build an owner-scoped keyword predicate for note fallback retrieval."""
        from sqlalchemy import and_, or_

        keyword_filters = [
            or_(
                note_model.title.ilike(f"%{keyword}%"),
                note_model.content.ilike(f"%{keyword}%"),
            )
            for keyword in query.split()
            if keyword
        ]
        if not keyword_filters:
            return note_model.user_id == user_id
        return and_(note_model.user_id == user_id, or_(*keyword_filters))

    async def _keyword_search_notes(self, query: str, top_k: int) -> List[Evidence]:
        """Search notes by keywords when vector retrieval is unavailable."""
        try:
            from app.db.db_config import AsyncSessionLocal
            from sqlalchemy import select
            from app.models.note import Note

            async with AsyncSessionLocal() as session:
                where_clause = self._build_note_keyword_filter(Note, self.user_id, query)
                result = await session.execute(
                    select(Note).where(where_clause).limit(top_k)
                )
                notes = list(result.scalars().all())

                evidences = []
                for i, note in enumerate(notes):
                    preview = note.content[:200] + "..." if len(note.content) > 200 else note.content
                    evidences.append(Evidence(
                        source_type=SourceType.NOTE,
                        source_id=note.id,
                        chunk_id=f"note_{note.id}",
                        title=note.title,
                        content=preview,
                        score=0.5 - i * 0.05,
                        metadata={
                            "user_id": note.user_id,
                            "note_id": note.id,
                        }
                    ))

                return evidences[:top_k]

        except Exception as e:
            logger.error(f"[Retrieval] keyword note fallback failed: {e}")
            return []

    async def _generate_hyde(self, query: str) -> str:
        """生成假设性文档（HyDE）"""
        try:
            from app.utils.factory import (
                get_default_chat_model,
                create_chat_model_from_config,
                sanitize_client_llm_config,
                llm_config_is_usable,
            )
            from langchain_core.prompts import PromptTemplate
            from langchain_core.output_parsers import StrOutputParser

            # 优先用前端传入的 llm_config，回退服务端默认模型
            config = sanitize_client_llm_config(self.llm_config)
            if llm_config_is_usable(config):
                chat_model = create_chat_model_from_config(config)
            else:
                chat_model = get_default_chat_model()
            hyde_prompt = PromptTemplate.from_template(
                "基于以下问题，生成一个详细的假设性回答，用于向量检索：\n\n问题：{query}\n\n假设性回答："
            )
            chain = hyde_prompt | chat_model | StrOutputParser()
            return await chain.ainvoke({"query": query})
        except Exception as e:
            logger.warning(f"【统一检索】HyDE 生成失败，使用原始查询: {e}")
            return query

    def _check_embedding(self) -> bool:
        """检查 embedding 服务是否可用"""
        try:
            from app.utils.factory import embed_model
            embed = embed_model.resolve()
            return embed is not None
        except Exception:
            return False

    def _deduplicate(self, evidences: List[Evidence]) -> List[Evidence]:
        """去重：基于 source_id 和 content"""
        seen = set()
        unique = []
        for ev in evidences:
            key = (ev.source_type, ev.source_id, ev.content[:100])
            if key not in seen:
                seen.add(key)
                unique.append(ev)
        return unique

    def _merge_adjacent(self, evidences: List[Evidence]) -> List[Evidence]:
        """
        合并相邻片段：同一文档的连续切片合并为一个证据。
        """
        if len(evidences) <= 1:
            return evidences

        merged = []
        current = evidences[0]

        for next_ev in evidences[1:]:
            # 同一文档的相邻切片
            if (
                current.source_type == next_ev.source_type
                and current.source_id == next_ev.source_id
                and current.title == next_ev.title
            ):
                # 合并内容
                current = Evidence(
                    source_type=current.source_type,
                    source_id=current.source_id,
                    chunk_id=current.chunk_id,
                    title=current.title,
                    content=current.content + "\n" + next_ev.content,
                    score=max(current.score, next_ev.score),
                    metadata=current.metadata,
                )
            else:
                merged.append(current)
                current = next_ev

        merged.append(current)
        return merged
