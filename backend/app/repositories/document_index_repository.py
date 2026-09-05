"""
文档索引记录仓库 -- 封装 document_index 表的 CRUD 操作。

SQL 注入防护：所有查询均通过 SQLAlchemy ORM 参数化执行，禁止拼接原始 SQL。
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_index import DocumentIndex, DocumentIndexStatus
from app.core.logger_handler import logger


class DocumentIndexRepository:
    """文档索引记录仓库"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: str,
        filename: str,
        original_filename: str,
        file_path: str,
        md5: str,
        file_size: int = None,
        file_type: str = None,
        space_id: str = None,
        status: DocumentIndexStatus = DocumentIndexStatus.UPLOADED,
    ) -> DocumentIndex:
        """创建文档索引记录"""
        doc = DocumentIndex(
            id=str(uuid.uuid4()),
            user_id=user_id,
            space_id=space_id,
            filename=filename,
            original_filename=original_filename,
            file_path=file_path,
            file_size=file_size,
            file_type=file_type,
            md5=md5,
            status=status,
        )
        self.session.add(doc)
        await self.session.flush()
        logger.info(f"【文档索引】创建记录: id={doc.id}, filename={filename}, status={status.value}")
        return doc

    async def get_by_id(self, doc_id: str, user_id: str) -> Optional[DocumentIndex]:
        """根据ID获取文档索引记录"""
        result = await self.session.execute(
            select(DocumentIndex).where(
                and_(DocumentIndex.id == doc_id, DocumentIndex.user_id == user_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_by_md5(self, md5: str, user_id: str) -> Optional[DocumentIndex]:
        """根据MD5获取文档索引记录（用于内容去重）"""
        result = await self.session.execute(
            select(DocumentIndex).where(
                and_(DocumentIndex.md5 == md5, DocumentIndex.user_id == user_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_by_original_filename(self, original_filename: str, user_id: str) -> Optional[DocumentIndex]:
        """根据原始文件名获取文档索引记录（用于文件名唯一性校验）。

        文件名唯一性按用户隔离：同一用户不能上传同名文件。
        """
        result = await self.session.execute(
            select(DocumentIndex).where(
                and_(DocumentIndex.original_filename == original_filename, DocumentIndex.user_id == user_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_user_documents(
        self, user_id: str, space_id: str = None, status: DocumentIndexStatus = None
    ) -> List[DocumentIndex]:
        """获取用户的所有文档索引记录"""
        conditions = [DocumentIndex.user_id == user_id]
        if space_id:
            conditions.append(DocumentIndex.space_id == space_id)
        if status:
            conditions.append(DocumentIndex.status == status)

        result = await self.session.execute(
            select(DocumentIndex).where(and_(*conditions)).order_by(DocumentIndex.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        doc_id: str,
        status: DocumentIndexStatus,
        error_message: str = None,
        chunk_count: int = None,
    ) -> None:
        """更新文档索引状态"""
        values = {"status": status, "updated_at": datetime.now(timezone.utc).replace(tzinfo=None)}
        if error_message is not None:
            values["error_message"] = error_message
        if chunk_count is not None:
            values["chunk_count"] = chunk_count
        if status == DocumentIndexStatus.INDEXED:
            values["indexed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

        await self.session.execute(
            update(DocumentIndex).where(DocumentIndex.id == doc_id).values(**values)
        )
        await self.session.flush()
        logger.info(f"【文档索引】更新状态: id={doc_id}, status={status.value}")

    async def claim_for_indexing(self, doc_id: str) -> Optional[int]:
        """原子抢占 INDEXING：仅当当前状态为 pending_index 时成功。

        两个 worker 同时领取同一文档时只有一个拿到（rowcount=1），
        另一个安全退出；已处于终态/正在索引的重复投递不再进入 INDEXING。

        R2B：抢占同时递增执行代次并返回新代次；返回 None 表示未抢占到。
        chunk ID 与向量元数据携带该代次，迟到/重复 worker 无法把旧代次
        产物当作新代次发布。
        """
        result = await self.session.execute(
            update(DocumentIndex)
            .where(DocumentIndex.id == doc_id)
            .where(DocumentIndex.status == DocumentIndexStatus.PENDING_INDEX)
            .values(
                status=DocumentIndexStatus.INDEXING,
                generation=DocumentIndex.generation + 1,
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        if result.rowcount == 0:
            await self.session.flush()
            return None
        gen_result = await self.session.execute(
            select(DocumentIndex.generation).where(DocumentIndex.id == doc_id)
        )
        await self.session.flush()
        return gen_result.scalar_one_or_none()

    async def publish_indexed(self, doc_id: str, chunk_count: int,
                              generation: Optional[int] = None) -> bool:
        """条件发布：仅当仍处于 indexing（未被 deleting/删除抢占）时置为 indexed。

        防止已领取 worker 在删除之后仍把状态改回 indexed（F04）。
        R2B：提供 generation 时额外要求代次匹配（执行身份），并把
        active_generation 推进到该代次——读取侧过滤与旧代次清理的依据。
        """
        values = {
            "status": DocumentIndexStatus.INDEXED,
            "chunk_count": chunk_count,
            "indexed_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        stmt = (
            update(DocumentIndex)
            .where(DocumentIndex.id == doc_id)
            .where(DocumentIndex.status == DocumentIndexStatus.INDEXING)
        )
        if generation is not None:
            stmt = stmt.where(DocumentIndex.generation == generation)
            values["active_generation"] = generation
        result = await self.session.execute(stmt.values(**values))
        await self.session.flush()
        return result.rowcount > 0

    async def mark_index_failed_if_indexing(self, doc_id: str, error_message: str,
                                            generation: Optional[int] = None) -> bool:
        """条件失败写回：仅当仍处于 indexing 时置为 index_failed。

        删除/deleting 已抢占时不覆盖其状态。
        R2B：提供 generation 时额外要求代次匹配（执行身份）。
        """
        stmt = (
            update(DocumentIndex)
            .where(DocumentIndex.id == doc_id)
            .where(DocumentIndex.status == DocumentIndexStatus.INDEXING)
        )
        if generation is not None:
            stmt = stmt.where(DocumentIndex.generation == generation)
        result = await self.session.execute(
            stmt.values(
                status=DocumentIndexStatus.INDEX_FAILED,
                error_message=error_message,
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await self.session.flush()
        return result.rowcount > 0

    async def get_indexed_md5s(self, user_id: str, md5s: List[str]) -> dict:
        """查询仍处于 indexed 状态的 md5 → active_generation 映射（R2D 读取侧过滤）。"""
        if not md5s:
            return {}
        result = await self.session.execute(
            select(DocumentIndex.md5, DocumentIndex.active_generation).where(
                DocumentIndex.user_id == user_id,
                DocumentIndex.md5.in_(md5s),
                DocumentIndex.status == DocumentIndexStatus.INDEXED,
            )
        )
        return {md5: (gen or 0) for md5, gen in result.all()}

    async def increment_retry(self, doc_id: str) -> None:
        """增加重试次数"""
        result = await self.session.execute(
            select(DocumentIndex).where(DocumentIndex.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.retry_count = (doc.retry_count or 0) + 1
            doc.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await self.session.flush()

    async def get_status(self, doc_id: str, user_id: str) -> Optional[DocumentIndexStatus]:
        """仅查询状态列（绕开 identity map，读取数据库当前真实值）。"""
        result = await self.session.execute(
            select(DocumentIndex.status).where(
                and_(DocumentIndex.id == doc_id, DocumentIndex.user_id == user_id)
            )
        )
        return result.scalar_one_or_none()

    async def reset_to_pending_if_failed(self, doc_id: str) -> bool:
        """条件重试重置：仅当仍处于 index_failed 时回到 pending_index。

        worker 失败重试路径专用：失败写回后状态可能已被删除/deleting 抢占
        （S02），此时不得重置、不得投递重试。
        """
        result = await self.session.execute(
            update(DocumentIndex)
            .where(DocumentIndex.id == doc_id)
            .where(DocumentIndex.status == DocumentIndexStatus.INDEX_FAILED)
            .values(
                status=DocumentIndexStatus.PENDING_INDEX,
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await self.session.flush()
        return result.rowcount > 0

    async def reset_to_pending_if_retryable(self, doc_id: str) -> bool:
        """条件人工重试重置：仅 index_failed / pending_index 可重置。

        人工重试入口先读后写之间存在 TOCTOU（S02）：读取时是 failed，
        更新前可能已被置为 deleting/indexing。条件更新以行数为准。
        """
        result = await self.session.execute(
            update(DocumentIndex)
            .where(DocumentIndex.id == doc_id)
            .where(DocumentIndex.status.in_([
                DocumentIndexStatus.INDEX_FAILED,
                DocumentIndexStatus.PENDING_INDEX,
            ]))
            .values(
                status=DocumentIndexStatus.PENDING_INDEX,
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await self.session.flush()
        return result.rowcount > 0

    async def delete_by_id(self, doc_id: str, user_id: str) -> bool:
        """删除文档索引记录"""
        doc = await self.get_by_id(doc_id, user_id)
        if not doc:
            return False
        await self.session.delete(doc)
        await self.session.flush()
        return True

    async def get_recent_indexed_documents(self, limit: int = 50) -> List[DocumentIndex]:
        """获取最近的 indexed 文档（供周期性旧代次向量回收扫描，R2D）。"""
        result = await self.session.execute(
            select(DocumentIndex)
            .where(DocumentIndex.status == DocumentIndexStatus.INDEXED)
            .order_by(DocumentIndex.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_pending_documents(self, limit: int = 10) -> List[DocumentIndex]:
        """获取待索引的文档（用于批量索引）"""
        result = await self.session.execute(
            select(DocumentIndex)
            .where(DocumentIndex.status == DocumentIndexStatus.PENDING_INDEX)
            .order_by(DocumentIndex.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_failed_documents(self, user_id: str) -> List[DocumentIndex]:
        """获取索引失败的文档（用于重试）"""
        result = await self.session.execute(
            select(DocumentIndex).where(
                and_(
                    DocumentIndex.user_id == user_id,
                    DocumentIndex.status == DocumentIndexStatus.INDEX_FAILED
                )
            ).order_by(DocumentIndex.created_at.desc())
        )
        return list(result.scalars().all())
