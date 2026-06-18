"""
笔记服务层 —— 包含 CRUD、向量双写、异步自动标签等核心业务逻辑。

核心功能：
- 笔记 CRUD（MySQL 存储）
- 向量双写（ChromaDB notes_collection）
- 异步自动标签生成（LLM 后台任务）
- 语义搜索（基于向量嵌入）
- 关联推荐（同时搜索笔记库和知识库）

艾宾浩斯遗忘曲线间隔：[1, 2, 4, 7, 15, 30] 天
"""
import uuid
import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import HumanMessage

from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.schemas.models import NoteCreate, NoteUpdate, NoteResponse
from app.core.logger_handler import logger
from app.utils.prompt_loader import load_prompt
from app.services.note_vector_index import NoteVectorIndex
from app.repositories.note_repository import NoteRepository

NOTES_COLLECTION_NAME = "notes_collection"

# 艾宾浩斯间隔重复数组（天）
INTERVALS = [1, 2, 4, 7, 15, 30]


def _get_next_interval(review_count: int) -> int:
    """
    根据回顾次数返回下一次回顾间隔天数。
    超出预定义数组后固定使用 30 天间隔。
    """
    if review_count < len(INTERVALS):
        return INTERVALS[review_count]
    return INTERVALS[-1]


class NoteService:
    """
    笔记服务 —— 管理笔记的完整生命周期。

    双写机制：
    - MySQL: 存储笔记元数据（标题、内容、标签、分类）
    - ChromaDB: 存储笔记向量（用于语义搜索）

    异步任务：
    - 创建笔记后，后台异步调用 LLM 生成标签和分类
    - 创建回顾记录（艾宾浩斯遗忘曲线）
    """

    def __init__(self):
        """
        初始化笔记服务 —— 通过 NoteVectorIndex 管理笔记向量。

        使用独立的 collection（notes_collection）存储笔记向量，
        与知识库的 collection（rag_collection）分开。
        """
        self.note_index = NoteVectorIndex()
        self.note_repo = NoteRepository()

    @property
    def notes_store(self):
        """兼容现有 RagService 和测试代码，返回底层 Chroma 实例"""
        return self.note_index.store

    def _doc_to_response(self, note: Note) -> NoteResponse:
        """
        将 SQLAlchemy ORM 对象转换为 Pydantic 响应模型。
        """
        return NoteResponse(
            id=note.id,
            user_id=note.user_id,
            title=note.title,
            content=note.content,
            tags=note.tags if note.tags else None,
            category=note.category,
            created_at=str(note.created_at) if note.created_at else None,
            updated_at=str(note.updated_at) if note.updated_at else None,
        )

    async def create_note(self, db: AsyncSession, user_id: str, payload: NoteCreate, llm_config: dict = None) -> NoteResponse:
        """
        创建笔记 —— 双写 MySQL + ChromaDB。

        执行流程：
        1. 生成 UUID 作为笔记ID
        2. MySQL 写入笔记（支持用户指定 category/tags）
        3. ChromaDB 写入向量（用于语义搜索）
        4. 立即返回笔记 ID（不等待后台任务）
        5. 后台异步任务：
           - 如果用户未指定 category/tags → 调用 LLM 自动生成
           - 创建回顾记录（艾宾浩斯遗忘曲线）

        :param db: 数据库会话
        :param user_id: 用户ID
        :param payload: 笔记创建请求（title, content, category, tags）
        :param llm_config: 前端 LLM 配置
        :return: 笔记响应模型
        """
        note_id = str(uuid.uuid4())
        note = Note(
            id=note_id,
            user_id=user_id,
            title=payload.title,
            content=payload.content,
            category=payload.category,
            tags=payload.tags,
        )
        await self.note_repo.add(db, note)
        await db.commit()
        await db.refresh(note)

        # 向量化写入 ChromaDB
        try:
            await asyncio.to_thread(
                self.note_index.add_note, note_id, user_id, payload.title, payload.content
            )
        except Exception as e:
            logger.error(f"笔记向量化失败 note_id={note_id}: {e}")

        # 标签生成已移至 note_router.py，由 Celery 任务处理

        return self._doc_to_response(note)

    async def update_note(self, db: AsyncSession, note_id: str, user_id: str, payload: NoteUpdate) -> Optional[NoteResponse]:
        """
        更新笔记：
        1. 更新 MySQL 中的 title/content
        2. 如果 content 变更，删除旧向量并写入新向量
        """
        note = await self.note_repo.get_by_id(db, note_id, user_id)
        if not note:
            return None

        content_changed = payload.content is not None

        if payload.title is not None:
            note.title = payload.title
        if payload.content is not None:
            note.content = payload.content

        await db.commit()
        await db.refresh(note)

        # content 变更时同步更新向量
        if content_changed:
            try:
                await asyncio.to_thread(
                    self.note_index.update_note, note_id, user_id, note.title, note.content
                )
            except Exception as e:
                logger.error(f"更新笔记向量失败 note_id={note_id}: {e}")

        return self._doc_to_response(note)

    async def delete_note(self, db: AsyncSession, note_id: str, user_id: str) -> bool:
        """
        删除笔记：
        1. 删除 MySQL 中的笔记（review_records 通过 FK CASCADE 自动删除）
        2. 删除 ChromaDB 中的向量
        """
        deleted = await self.note_repo.delete_by_id(db, note_id, user_id)
        if not deleted:
            return False

        await db.commit()

        # 清理向量
        try:
            await asyncio.to_thread(self.note_index.delete_note, note_id, user_id)
        except Exception as e:
            logger.error(f"删除笔记向量失败 note_id={note_id}: {e}")

        return True

    async def get_note(self, db: AsyncSession, note_id: str, user_id: str) -> Optional[NoteResponse]:
        """
        根据笔记 ID 和用户 ID 获取笔记详情。
        """
        note = await self.note_repo.get_by_id(db, note_id, user_id)
        if not note:
            return None
        return self._doc_to_response(note)

    async def list_notes(
        self,
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        category: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> tuple[List[NoteResponse], int]:
        """
        分页查询笔记列表，支持按分类和标签筛选。
        tag 筛选：SQL 层分类过滤 + 内存层标签过滤（MySQL JSON_CONTAINS 在 async 驱动下绑定不可靠）。
        """
        notes, total = await self.note_repo.list_by_user_paged(
            db, user_id, page, page_size, category, tag
        )
        return [self._doc_to_response(n) for n in notes], total

    async def search_notes(self, db: AsyncSession, user_id: str, query: str, top_k: int = 10) -> List[NoteResponse]:
        """
        语义搜索笔记：ChromaDB 向量检索 → MySQL 回填完整数据。
        向量检索无结果时降级到 MySQL LIKE 模糊搜索。
        """
        note_ids = []
        try:
            note_ids = await asyncio.to_thread(
                self.note_index.search_user_notes, query, user_id, top_k
            )
        except Exception as e:
            logger.error(f"笔记语义搜索失败: {e}")

        # 向量检索有结果时使用向量顺序回填
        if note_ids:
            notes = await self.note_repo.get_by_ids(db, note_ids, user_id)
            notes_map = {n.id: n for n in notes}
            sorted_notes = []
            for nid in note_ids:
                if nid in notes_map:
                    sorted_notes.append(self._doc_to_response(notes_map[nid]))
            return sorted_notes

        # 降级：MySQL LIKE 模糊搜索
        notes = await self.note_repo.search_like(db, user_id, query, top_k)
        return [self._doc_to_response(n) for n in notes]

    async def search_related_notes(self, query: str, user_id: str, top_k: int = 5) -> List[dict]:
        """
        根据查询文本从笔记库中检索相关笔记（用于 AI 对话的"相关笔记"标签页）。
        仅使用 notes_collection 向量搜索，不查询 MySQL。
        """
        try:
            docs_with_scores = await asyncio.to_thread(
                self.note_index.search_related_notes, query, user_id, top_k
            )
        except Exception as e:
            logger.error(f"相关笔记搜索失败: {e}")
            return []

        results = []
        for doc, score in docs_with_scores:
            # ChromaDB similarity_search_with_score 返回距离（越小越相似）
            # 余弦距离：0=相同, 1=无关, 2=相反
            # 转换为 0-1 相似度
            similarity = max(0.0, min(1.0, 1.0 - score))
            results.append({
                "note_id": doc.metadata.get("note_id", ""),
                "title": doc.metadata.get("title", "无标题"),
                "content_preview": doc.page_content[:200],
                "similarity": round(similarity, 4),
            })

        return results

    async def get_related_notes(
        self,
        db: AsyncSession,
        note_id: str,
        user_id: str,
        top_k: int = 3,
    ) -> dict:
        """
        获取与当前笔记语义相似的其他笔记和知识库文档。

        检索流程：
        1. 用笔记内容同时在 notes_collection 和 rag_collection 检索
        2. 分别返回 notes 和 knowledge_docs 列表
        3. 按相似度降序排序
        """
        note = await self.get_note(db, note_id, user_id)
        if not note:
            return {"notes": [], "knowledge_docs": []}

        note_items = []
        kb_items = []

        # 从笔记库检索相似笔记（排除自身）
        try:
            note_docs = await asyncio.to_thread(
                self.note_index.find_related_for_note_content,
                note.content, user_id, note_id, top_k,
            )
            for doc, score in note_docs:
                note_items.append({
                    "id": doc.metadata.get("note_id", ""),
                    "title": doc.metadata.get("title", "无标题"),
                    "content_preview": doc.page_content[:150],
                    "similarity": round(score, 4),
                })
        except Exception as e:
            logger.error(f"从笔记库检索关联笔记失败: {e}")

        # 从知识库检索相关文档
        try:
            from app.rag.vector_store import VectorStoreService
            vector_store = VectorStoreService()
            kb_docs = await asyncio.to_thread(
                vector_store.vectors_store.similarity_search_with_score,
                note.content,
                k=top_k,
                filter={"user_id": user_id},
            )
            for doc, score in kb_docs:
                kb_items.append({
                    "id": doc.metadata.get("source", doc.metadata.get("filename", "")),
                    "title": doc.metadata.get("original_filename", doc.metadata.get("source", "知识库文档")),
                    "content_preview": doc.page_content[:150],
                    "content": doc.page_content,
                    "similarity": round(score, 4),
                })
        except Exception as e:
            logger.error(f"从知识库检索关联文档失败: {e}")

        # 按相似度降序排序（分数越低越相似），取 top_k
        note_items.sort(key=lambda x: x["similarity"])
        kb_items.sort(key=lambda x: x["similarity"])

        return {
            "notes": note_items[:top_k],
            "knowledge_docs": kb_items[:top_k],
        }

    @staticmethod
    def _extract_json(text: str) -> str:
        """
        从 LLM 输出中提取 JSON 字符串。
        处理以下情况：
        - JSON 被 markdown 代码块包裹（```json ... ```）
        - JSON 前面有文字描述
        - JSON 后面有文字描述
        """
        import re

        # 尝试匹配 markdown 代码块中的 JSON
        match = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 尝试从第一个 { 到最后一个 } 提取 JSON
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]

        return text

    async def _auto_tag_and_review(self, note_id: str, user_id: str, content: str, llm_config: dict = None):
        """
        后台异步任务：LLM 分析笔记内容 → 生成标签和分类 → 更新 MySQL → 创建回顾记录。
        优先读取 Redis 缓存，命中则跳过 LLM 调用。
        """
        try:
            from app.cache.llm_cache import get_cached_llm_response, set_cached_llm_response

            # 加载 prompt 模板并填充笔记内容
            prompt_template = load_prompt("auto_tag_prompt")
            prompt = prompt_template.replace("{content}", content)

            # 优先使用用户配置的模型
            from app.utils.factory import create_chat_model_from_config, llm_config_is_usable
            if llm_config_is_usable(llm_config):
                model = create_chat_model_from_config(llm_config)
                model_name = llm_config.get("model", "custom")
            else:
                from app.utils.factory import chat_model
                model = chat_model
                import os
                model_name = os.getenv("CHAT_MODEL_NAME", "default")
            from app.db.db_config import AsyncSessionLocal

            # 查缓存
            cached = await get_cached_llm_response(prompt, model_name)
            if cached:
                raw_output = cached
            else:
                response = await model.ainvoke([HumanMessage(content=prompt)])
                raw_output = response.content.strip()
                # 写缓存
                await set_cached_llm_response(prompt, model_name, raw_output)

            # 提取 JSON：LLM 输出可能包含前言、markdown代码块等
            json_str = self._extract_json(raw_output)

            # 解析 LLM 返回的 JSON
            result = json.loads(json_str)
            tags = result.get("tags", [])
            category = result.get("category", "life")

            logger.info(f"自动标签生成完成 note_id={note_id}, tags={tags}, category={category}")

            # 写入 MySQL
            async with AsyncSessionLocal() as session:
                await self.note_repo.update_tags_and_category(session, note_id, user_id, tags, category)

                # 创建回顾记录（首次间隔 1 天），避免重复
                existing = await session.execute(
                    select(ReviewRecord).where(
                        ReviewRecord.note_id == note_id,
                        ReviewRecord.user_id == user_id,
                    )
                )
                if not existing.scalar_one_or_none():
                    now = datetime.now()
                    review = ReviewRecord(
                        id=str(uuid.uuid4()),
                        note_id=note_id,
                        user_id=user_id,
                        next_review_at=now + timedelta(days=1),
                        interval_days=1,
                        review_count=0,
                    )
                    session.add(review)
                await session.commit()

        except json.JSONDecodeError as e:
            logger.error(f"解析 LLM 标签输出失败 note_id={note_id}, raw={raw_output[:200]}, extracted={json_str[:200]}: {e}")
        except Exception as e:
            logger.error(f"自动标签后台任务失败 note_id={note_id}: {e}")

    async def autocomplete(self, context: str, llm_config: dict = None) -> dict:
        """
        AI 内联补全 —— 基于光标前上下文，调用 LLM 快速生成续写文本。
        优先读取 Redis 缓存，命中则跳过 LLM 调用。
        """
        try:
            from langchain_core.messages import HumanMessage
            from app.cache.llm_cache import get_cached_llm_response, set_cached_llm_response

            from app.utils.factory import create_chat_model_from_config, llm_config_is_usable
            if llm_config_is_usable(llm_config):
                model = create_chat_model_from_config(llm_config)
                model_name = llm_config.get("model", "custom")
            else:
                from app.utils.factory import chat_model
                model = chat_model
                import os
                model_name = os.getenv("CHAT_MODEL_NAME", "default")

            prompt_template = load_prompt("autocomplete_prompt")
            prompt = prompt_template.format(context=context[-200:])

            # 查缓存
            cached = await get_cached_llm_response(prompt, model_name)
            if cached:
                return {"success": True, "completion": cached}

            response = await model.ainvoke([HumanMessage(content=prompt)])
            completion = response.content.strip()

            # 防止回复重复已有内容
            if completion and context.endswith(completion[:10]):
                completion = completion[10:]

            # 写缓存
            await set_cached_llm_response(prompt, model_name, completion)

            return {"success": True, "completion": completion}
        except Exception as e:
            logger.error(f"内联补全失败: {e}")
            return {"success": False, "completion": ""}

    async def assist_stream(self, content: str, action: str, llm_config: dict = None):
        """
        AI 写作辅助 SSE 流式输出 —— 支持续写/缩写/扩写三种模式。

        Args:
            content: 用户选中的文本
            action: 操作类型 (expand / summarize / continue)

        Yields:
            SSE 事件数据（字符串）
        """
        from langchain_core.messages import HumanMessage

        from app.utils.factory import create_chat_model_from_config, llm_config_is_usable
        if llm_config_is_usable(llm_config):
            model = create_chat_model_from_config(llm_config)
        else:
            from app.utils.factory import chat_model
            model = chat_model

        prompt_template = load_prompt("write_assistant_prompt")
        prompt = prompt_template.format(content=content, action=action)

        try:
            async for chunk in model.astream([HumanMessage(content=prompt)]):
                if chunk.content:
                    yield f"data: {chunk.content}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"写作辅助流式输出失败: {e}")
            yield f"data: [ERROR: {str(e)}]\n\n"

    async def get_category_stats(self, db: AsyncSession, user_id: str) -> dict:
        """获取用户的笔记分类统计"""
        category_map = await self.note_repo.get_category_counts(db, user_id)
        total = sum(category_map.values())

        categories = []
        for cat in ['work', 'study', 'life', 'project']:
            categories.append({"category": cat, "count": category_map.get(cat, 0)})

        return {
            "total": total,
            "categories": categories,
            "uncategorized": category_map.get(None, 0),
        }

    async def export_note_markdown(self, db: AsyncSession, note_id: str, user_id: str) -> Optional[str]:
        """
        导出单篇笔记为 Markdown 文本。
        包含 frontmatter 格式的元数据（标题、标签、分类、日期）。
        """
        note = await self.get_note(db, note_id, user_id)
        if not note:
            return None

        lines = ["---"]
        lines.append(f"title: {note.title}")
        if note.tags:
            lines.append(f"tags: [{', '.join(note.tags)}]")
        if note.category:
            lines.append(f"category: {note.category}")
        lines.append(f"created_at: {note.created_at}")
        lines.append(f"updated_at: {note.updated_at}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {note.title}")
        lines.append("")
        lines.append(note.content)

        return "\n".join(lines)


note_service = NoteService()


def get_note_service() -> NoteService:
    """依赖注入工厂函数。"""
    return note_service
