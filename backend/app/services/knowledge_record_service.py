"""
知识库记录管理服务 —— MD5 去重记录和用户文档清理。

职责：
- 删除用户所有上传向量文档
- 清空用户 MD5 去重记录
- 删除单条 MD5 记录
- 按文件名删除 MD5 记录
- 查询 MD5 记录详情
- 获取用户所有 MD5 记录

初始化方式：
- 通过 store_factory 延迟创建 VectorStoreService，避免在 KnowledgeService
  构造时提前初始化 Chroma 等重依赖。
"""
from app.core.logger_handler import logger


class KnowledgeRecordService:
    """知识库记录管理 —— 封装 MD5 / 去重记录的 CRUD 操作"""

    def __init__(self, store_factory=None):
        """
        :param store_factory: 返回 VectorStoreService 实例的可调用对象。
            默认为 None，在首次调用时懒加载为 VectorStoreService 类本身。
        """
        if store_factory is None:
            from app.rag.vector_store import VectorStoreService
            store_factory = VectorStoreService
        self._store_factory = store_factory

    def _get_store(self):
        """延迟创建 VectorStoreService 实例"""
        return self._store_factory()

    async def clean_user_upload(self, user_id: str) -> None:
        """删除指定用户的所有上传向量文档"""
        await self._get_store().delete_user_documents(user_id)

    async def clear_user_md5(self, user_id: str, delete_documents: bool = True) -> None:
        """
        清空用户的所有 MD5 去重记录。

        参数:
            user_id (str): 目标用户的 ID。
            delete_documents (bool): 是否同时删除对应的向量文档。
        """
        store = self._get_store()
        await store.delete_user_md5(user_id, delete_documents)
        if delete_documents:
            logger.info(f"【知识库】清空用户 {user_id} 的MD5记录和文档")
        else:
            logger.info(f"【知识库】清空用户 {user_id} 的MD5记录（保留知识库文档）")

    async def delete_single_md5(self, user_id: str, md5_value: str, delete_documents: bool = True) -> bool:
        """
        删除用户的单条 MD5 记录。

        返回:
            bool: 删除成功返回 True，记录不存在或删除失败返回 False。
        """
        success = await self._get_store().delete_single_md5(user_id, md5_value, delete_documents)
        if success:
            logger.info(f"【知识库】删除用户 {user_id} 的MD5记录: {md5_value}")
        else:
            logger.warning(f"【知识库】删除用户 {user_id} 的MD5记录失败: {md5_value}")
        return success

    async def delete_by_filename(self, user_id: str, filename: str, delete_documents: bool = True) -> bool:
        """
        按文件名删除用户的知识库文件。

        返回:
            bool: 删除成功返回 True，文件不存在或删除失败返回 False。
        """
        success = await self._get_store().delete_by_filename(user_id, filename, delete_documents)
        if success:
            logger.info(f"【知识库】删除用户 {user_id} 的文件: {filename}")
        else:
            logger.warning(f"【知识库】删除用户 {user_id} 的文件失败: {filename}")
        return success

    async def get_md5_info(self, user_id: str, md5_value: str) -> dict:
        """查询指定 MD5 记录的详细信息"""
        return await self._get_store().get_md5_info(user_id, md5_value)

    async def get_all_md5_records(self, user_id: str) -> list:
        """获取用户的所有 MD5 去重记录列表"""
        return await self._get_store().get_all_md5_records(user_id)
