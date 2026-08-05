"""
笔记向量索引 —— 封装 ChromaDB notes_collection 的所有向量操作。

职责：
- 笔记向量的增删改查
- 所有搜索方法内部强制加 user_id + doc_type 过滤，防止跨用户泄露
- 与 NoteService 解耦，仅负责向量层

过滤规则：
    filter={"$and": [{"user_id": user_id}, {"doc_type": "note"}]}
"""
import asyncio
from typing import List, Optional, Tuple

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.utils.factory import embed_model
from app.utils.config import chroma_config
from app.utils.path_tool import get_abstract_path
from app.core.logger_handler import logger

NOTES_COLLECTION_NAME = "notes_collection"


def _user_note_filter(user_id: str) -> dict:
    """构建用户笔记过滤条件，防止跨用户泄露"""
    return {"$and": [{"user_id": user_id}, {"doc_type": "note"}]}


class NoteVectorIndex:
    """
    笔记向量索引 —— 管理 notes_collection 的所有向量操作。

    所有搜索方法强制带 user_id 过滤，调用方无需手动构建 filter。
    """

    def __init__(self):
        # ChromaDB ??? Rust DLL???????????????????
        # ?? C ?????? VC++ Runtime ????/??????????
        self._store = None

    def _ensure_store(self) -> Chroma:
        if self._store is None:
            persist_dir = get_abstract_path(chroma_config['persist_directory'])
            self._store = Chroma(
                collection_name=NOTES_COLLECTION_NAME,
                embedding_function=embed_model,
                persist_directory=persist_dir,
            )
        return self._store

    @property
    def store(self) -> Chroma:
        """返回底层 Chroma 实例，供检索服务使用"""
        return self._ensure_store()

    @staticmethod
    def _note_document(note_id: str, user_id: str, title: str, content: str) -> Document:
        return Document(
            page_content=content,
            metadata={
                "user_id": user_id,
                "note_id": note_id,
                "doc_type": "note",
                "title": title,
            },
        )

    def add_note(self, note_id: str, user_id: str, title: str, content: str) -> None:
        """Add one note to Chroma."""
        self._ensure_store().add_documents(
            [self._note_document(note_id, user_id, title, content)], ids=[note_id]
        )

    def upsert_note(self, note_id: str, user_id: str, title: str, content: str) -> None:
        """Idempotently synchronize one note; safe for repeated queue tasks."""
        store = self._ensure_store()
        store.delete(where={"$and": [
            {"note_id": note_id},
            {"user_id": user_id},
            {"doc_type": "note"},
        ]})
        store.add_documents(
            [self._note_document(note_id, user_id, title, content)], ids=[note_id]
        )

    def update_note(self, note_id: str, user_id: str, title: str, content: str) -> None:
        """Compatibility wrapper for existing callers."""
        self.upsert_note(note_id, user_id, title, content)

    def delete_note(self, note_id: str, user_id: str) -> None:
        """Delete one user's note vector."""
        self._ensure_store().delete(where={"$and": [
            {"note_id": note_id},
            {"user_id": user_id},
            {"doc_type": "note"},
        ]})

    def search_user_notes(self, query: str, user_id: str, top_k: int = 10) -> List[str]:
        """
        语义搜索当前用户的笔记，返回 note_id 列表（按相似度排序）。
        """
        docs = self._store.similarity_search(
            query,
            k=top_k,
            filter=_user_note_filter(user_id),
        )
        return [doc.metadata.get("note_id") for doc in docs if doc.metadata.get("note_id")]

    def search_related_notes(
        self, query: str, user_id: str, top_k: int = 5
    ) -> List[Tuple[Document, float]]:
        """
        搜索相关笔记，返回 (Document, score) 列表。
        用于 AI 对话的"相关笔记"标签页。
        """
        return self._store.similarity_search_with_score(
            query,
            k=top_k,
            filter=_user_note_filter(user_id),
        )

    def find_related_for_note_content(
        self,
        content: str,
        user_id: str,
        exclude_note_id: str,
        top_k: int = 3,
    ) -> List[Tuple[Document, float]]:
        """
        根据笔记内容检索关联笔记，排除自身。
        用于 get_related_notes 方法。
        """
        docs_with_scores = self._store.similarity_search_with_score(
            content,
            k=top_k + 1,  # 多取一个，排除自身
            filter=_user_note_filter(user_id),
        )
        return [
            (doc, score)
            for doc, score in docs_with_scores
            if doc.metadata.get("note_id") != exclude_note_id
        ]
