"""
受控检索工具 -- Agent 可调用的工具集合。

所有工具都强制用户和空间隔离，确保数据安全。
"""
import asyncio
from typing import List, Optional, Dict, Any

from app.core.logger_handler import logger
from app.rag.retrieval_service import RetrievalService


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

    读取侧验证（评审 P1）：
    1. 切片 metadata.user_id 必须等于当前用户（缺失标识一律拒绝，fail-closed）；
    2. 切片所属文档（按 md5）必须仍处于 indexed；
    3. 切片携带 generation 时必须等于文档的 active_generation —— 旧代次/
       已删除文档的切片不得通过 chunk_id 直读复活。
    任一验证失败返回 None，不返回内容。
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

        metadata = result['metadatas'][0] if result['metadatas'] else {}
        content = result['documents'][0] if result['documents'] else ""

        # 所有权验证：metadata 必须携带 user_id 且与请求者一致
        chunk_owner = metadata.get("user_id")
        if not chunk_owner or chunk_owner != user_id:
            logger.warning(
                f"【工具】get_document_chunk 拒绝：切片不存在或不属于当前用户 "
                f"chunk_id={chunk_id}, user_id={user_id}"
            )
            return None

        # 可见性验证：所属文档必须仍为 indexed，且代次与已发布代次一致
        md5 = metadata.get("md5", "")
        if not md5:
            logger.warning(f"【工具】get_document_chunk 拒绝：切片缺少 md5 标识 chunk_id={chunk_id}")
            return None

        from app.services.document_index_service import get_active_document_md5s
        active = await get_active_document_md5s(user_id, [md5])
        active_generation = active.get(md5)
        if active_generation is None:
            logger.warning(
                f"【工具】get_document_chunk 拒绝：所属文档不可见（已删除/未发布）"
                f"chunk_id={chunk_id}, md5={md5}"
            )
            return None
        chunk_generation = metadata.get("generation")
        if chunk_generation is not None:
            if int(chunk_generation) != int(active_generation):
                logger.warning(
                    f"【工具】get_document_chunk 拒绝：旧代次切片 chunk_id={chunk_id}, "
                    f"chunk_gen={chunk_generation}, active_gen={active_generation}"
                )
                return None
        elif active_generation > 0:
            # 文档已发布过代次但切片无代次元数据：代次化之前的残留，拒绝
            logger.warning(
                f"【工具】get_document_chunk 拒绝：无代次残留切片 chunk_id={chunk_id}"
            )
            return None

        return {
            "chunk_id": chunk_id,
            "content": content,
            "metadata": metadata,
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
