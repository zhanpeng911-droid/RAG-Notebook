"""
文档索引记录仓库 -- 封装 document_index 表的 CRUD 操作。

SQL 注入防护：所有查询均通过 SQLAlchemy ORM 参数化执行，禁止拼接原始 SQL。
"""
import uuid
from datetime import datetime
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
        values = {"status": status, "updated_at": datetime.utcnow()}
        if error_message is not None:
            values["error_message"] = error_message
        if chunk_count is not None:
            values["chunk_count"] = chunk_count
        if status == DocumentIndexStatus.INDEXED:
            values["indexed_at"] = datetime.utcnow()

        await self.session.execute(
            update(DocumentIndex).where(DocumentIndex.id == doc_id).values(**values)
        )
        await self.session.flush()
        logger.info(f"【文档索引】更新状态: id={doc_id}, status={status.value}")

    async def increment_retry(self, doc_id: str) -> None:
        """增加重试次数"""
        result = await self.session.execute(
            select(DocumentIndex).where(DocumentIndex.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.retry_count = (doc.retry_count or 0) + 1
            doc.updated_at = datetime.utcnow()
            await self.session.flush()

    async def delete_by_id(self, doc_id: str, user_id: str) -> bool:
        """删除文档索引记录"""
        doc = await self.get_by_id(doc_id, user_id)
        if not doc:
            return False
        await self.session.delete(doc)
        await self.session.flush()
        return True

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
