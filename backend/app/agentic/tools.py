"""
受控检索工具 -- Agent 可调用的工具集合。

所有工具都强制用户和空间隔离，确保数据安全。
"""
import asyncio
from typing import List, Optional, Dict, Any

from app.core.logger_handler import logger
from app.rag.retrieval_service import RetrievalService, Evidence


async def search_knowledge(
    query: str,
    user_id: str,
    space_id: str = None,
    top_k: int = 8,
) -> List[Dict[str, Any]]:
    """
    查询知识库。

    :param query: 查询文本
    :param user_id: 用户 ID（强制）
    :param space_id: 空间 ID（可选）
    :param top_k: 返回结果数
    :return: 证据列表（字典格式）
    """
    logger.info(f"【工具】search_knowledge: query={query[:50]}, user_id={user_id}")

    service = RetrievalService(user_id=user_id, space_id=space_id)
    evidences = await service.retrieve(
        query=query,
        scope="knowledge" if not space_id else f"space:{space_id}",
        top_k=top_k,
    )

    return [ev.to_dict() for ev in evidences]


async def search_notes(
    query: str,
    user_id: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    查询个人笔记。

    :param query: 查询文本
    :param user_id: 用户 ID（强制）
    :param top_k: 返回结果数
    :return: 证据列表（字典格式）
    """
    logger.info(f"【工具】search_notes: query={query[:50]}, user_id={user_id}")

    service = RetrievalService(user_id=user_id)
    evidences = await service.retrieve(
        query=query,
        scope="notes",
        top_k=top_k,
    )

    return [ev.to_dict() for ev in evidences]


async def search_all(
    query: str,
    user_id: str,
    space_id: str = None,
    top_k: int = 8,
) -> List[Dict[str, Any]]:
    """
    混合检索（知识库 + 笔记）。

    :param query: 查询文本
    :param user_id: 用户 ID（强制）
    :param space_id: 空间 ID（可选）
    :param top_k: 返回结果数
    :return: 证据列表（字典格式）
    """
    logger.info(f"【工具】search_all: query={query[:50]}, user_id={user_id}")

    service = RetrievalService(user_id=user_id, space_id=space_id)
    evidences = await service.retrieve(
        query=query,
        scope="all",
        top_k=top_k,
    )

    return [ev.to_dict() for ev in evidences]


async def get_document_chunk(
    chunk_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    获取命中文档块上下文（仅限本次命中内容）。

    :param chunk_id: 切片 ID
    :param user_id: 用户 ID（强制）
    :return: 切片详情
    """
    logger.info(f"【工具】get_document_chunk: chunk_id={chunk_id}, user_id={user_id}")

    # 从向量库获取切片
    try:
        from app.rag.vector_store import VectorStoreService
        store = VectorStoreService()

        # 获取切片
        result = await asyncio.to_thread(
            store.vectors_store.get,
            ids=[chunk_id],
            include=['documents', 'metadatas']
        )

        if not result['ids']:
            return None

        return {
            "chunk_id": chunk_id,
            "content": result['documents'][0] if result['documents'] else "",
            "metadata": result['metadatas'][0] if result['metadatas'] else {},
        }
    except Exception as e:
        logger.error(f"【工具】获取切片失败: {e}")
        return None


async def list_user_documents(
    query: str,
    user_id: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    按标题查找文件（仅返回元数据，不返回内容）。

    :param query: 搜索关键词
    :param user_id: 用户 ID（强制）
    :param limit: 返回数量
    :return: 文档元数据列表
    """
    logger.info(f"【工具】list_user_documents: query={query[:50]}, user_id={user_id}")

    try:
        from app.db.db_config import AsyncSessionLocal
        from app.repositories.document_index_repository import DocumentIndexRepository
        from sqlalchemy import select
        from app.models.document_index import DocumentIndex

        async with AsyncSessionLocal() as session:
            repo = DocumentIndexRepository(session)
            docs = await repo.get_user_documents(user_id)

            # 关键词过滤
            keywords = query.lower().split()
            matched = []
            for doc in docs:
                title = doc.original_filename.lower()
                if any(kw in title for kw in keywords):
                    matched.append({
                        "id": doc.id,
                        "filename": doc.original_filename,
                        "file_type": doc.file_type,
                        "status": doc.status.value,
                        "created_at": doc.created_at.isoformat() if doc.created_at else None,
                    })

            return matched[:limit]

    except Exception as e:
        logger.error(f"【工具】列出文档失败: {e}")
        return []


# 工具注册表
AGENT_TOOLS = {
    "search_knowledge": search_knowledge,
    "search_notes": search_notes,
    "search_all": search_all,
    "get_document_chunk": get_document_chunk,
    "list_user_documents": list_user_documents,
}
