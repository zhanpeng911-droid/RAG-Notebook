"""
笔记数据访问层 —— 封装所有 Note 的 MySQL/SQLAlchemy 查询。

职责：
- Note 的 CRUD 数据库操作
- Note 列表分页查询
- Note 分类统计
- Note fallback LIKE 搜索
- 根据 note_ids 回填 Note 列表

设计原则：
- 只负责数据库访问，不负责业务编排
- 默认不做 commit，事务边界由调用方（NoteService）控制
- 所有读取/更新/删除单用户数据的方法都必须带 user_id 条件
"""
from typing import List, Optional, Tuple

from sqlalchemy import select, delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note


class NoteRepository:
    """笔记数据访问层"""

    async def get_by_id(self, db: AsyncSession, note_id: str, user_id: str) -> Optional[Note]:
        """根据 ID 和用户 ID 获取笔记"""
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, db: AsyncSession, note: Note) -> None:
        """添加笔记（不 commit，由调用方控制事务）"""
        db.add(note)

    async def delete_by_id(self, db: AsyncSession, note_id: str, user_id: str) -> bool:
        """删除笔记，返回是否成功"""
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id)
        result = await db.execute(stmt)
        note = result.scalar_one_or_none()
        if not note:
            return False
        await db.execute(delete(Note).where(Note.id == note_id, Note.user_id == user_id))
        return True

    async def list_by_user(
        self,
        db: AsyncSession,
        user_id: str,
        category: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Tuple[List[Note], int]:
        """
        查询用户笔记列表。

        tag 过滤在内存层完成（MySQL JSON_CONTAINS 在 async 驱动下绑定不可靠）。
        返回 (笔记列表, 总数)。分页由调用方控制。
        """
        conditions = [Note.user_id == user_id]
        if category:
            conditions.append(Note.category == category)

        if tag:
            # tag 过滤：先按分类条件查出全部，内存过滤后返回
            stmt = (
                select(Note)
                .where(*conditions)
                .order_by(Note.updated_at.desc())
            )
            result = await db.execute(stmt)
            all_notes = list(result.scalars().all())
            filtered = [n for n in all_notes if n.tags and tag in n.tags]
            total = len(filtered)
            return filtered, total
        else:
            # 无 tag 过滤时，SQL 层直接统计和分页
            count_stmt = select(func.count(Note.id)).where(*conditions)
            result = await db.execute(count_stmt)
            total = result.scalar() or 0

            stmt = (
                select(Note)
                .where(*conditions)
                .order_by(Note.updated_at.desc())
            )
            result = await db.execute(stmt)
            notes = list(result.scalars().all())
            return notes, total

    async def list_by_user_paged(
        self,
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        category: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Tuple[List[Note], int]:
        """分页查询用户笔记列表"""
        notes, total = await self.list_by_user(db, user_id, category, tag)

        if tag:
            # tag 过滤已在 list_by_user 内存完成，这里做分页切片
            start = (page - 1) * page_size
            return notes[start:start + page_size], total
        else:
            # 无 tag 时 SQL 层分页
            conditions = [Note.user_id == user_id]
            if category:
                conditions.append(Note.category == category)
            stmt = (
                select(Note)
                .where(*conditions)
                .order_by(Note.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all()), total

    async def get_by_ids(self, db: AsyncSession, note_ids: List[str], user_id: str) -> List[Note]:
        """根据 note_ids 列表回填笔记，带 user_id 过滤"""
        if not note_ids:
            return []
        stmt = select(Note).where(Note.id.in_(note_ids), Note.user_id == user_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def search_like(self, db: AsyncSession, user_id: str, query: str, limit: int = 10) -> List[Note]:
        """MySQL LIKE 模糊搜索（向量检索降级方案）"""
        like_pattern = f"%{query}%"
        stmt = (
            select(Note)
            .where(
                Note.user_id == user_id,
                (Note.title.ilike(like_pattern)) | (Note.content.ilike(like_pattern)),
            )
            .order_by(Note.updated_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_category_counts(self, db: AsyncSession, user_id: str) -> dict:
        """获取用户笔记分类统计"""
        stmt = (
            select(
                Note.category,
                func.count(Note.id).label("count"),
            )
            .where(Note.user_id == user_id)
            .group_by(Note.category)
        )
        result = await db.execute(stmt)
        rows = result.all()
        return {row.category: row.count for row in rows}

    async def update_tags_and_category(
        self, db: AsyncSession, note_id: str, user_id: str, tags: list, category: str
    ) -> None:
        """更新笔记的标签和分类（LLM 自动标签回调使用）"""
        stmt = (
            update(Note)
            .where(Note.id == note_id, Note.user_id == user_id)
            .values(tags=tags, category=category)
        )
        await db.execute(stmt)
