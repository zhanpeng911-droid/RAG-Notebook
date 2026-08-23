import asyncio
import os
import threading
import shutil

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.utils.config import chroma_config
from app.utils.factory import embed_model
from app.utils.path_tool import get_abstract_path
from app.core.logger_handler import logger

from .retrievers.hybrid_retriever import HybridRetriever
from .md5_manager import MD5Store
from .document_handler import DocumentProcessor
from app.utils.image_extractor import delete_image_directory, delete_user_all_images


def _clear_chroma_cache():
    """
    清除 ChromaDB SharedSystemClient 内部单例缓存，避免 KeyError。
    ChromaDB 在 0.5.x+ 引入了 SharedSystemClient，它内部维护了一个全局 _instance 字典。
    当同一个进程反复创建/删除 Chroma 实例时，会抛出 KeyError（因为缓存中的 client 已被销毁）。
    在初始化前主动清除缓存，可以避免此问题。
    """
    try:
        from chromadb.api.shared_system_client import SharedSystemClient
        SharedSystemClient.clear_system_cache()
    except (ImportError, AttributeError):
        pass


def reset_chroma_db_explicit(persist_dir: str | None = None):
    """
    显式重置 Chroma 数据目录（运维/管理操作，禁止在普通初始化路径调用）。

    会删除磁盘上的 persist 目录并清理内存缓存。调用方必须确认已备份。
    """
    target = persist_dir or get_abstract_path(chroma_config['persist_directory'])
    _clear_chroma_cache()
    if os.path.exists(target):
        shutil.rmtree(target)
        logger.warning(f"已显式删除 Chroma 数据库目录: {target}")


class VectorStoreService:
    """
    向量数据库服务 —— ChromaDB 的统一封装。

    设计模式：双重检查锁定单例
    - 为什么用单例：ChromaDB 客户端维护内部连接池和缓存，多实例会冲突
    - 为什么双重检查：避免每次请求都加锁，提升性能

    失败策略：
    - 初始化失败时保留磁盘数据，标记 degraded，不自动删除
    - 删除/重建仅允许通过 reset_chroma_db_explicit 显式执行

    核心功能：
    - 文档向量化存储
    - 混合检索（向量 + BM25）
    - MD5 记录管理（去重）
    - 文档 CRUD 操作
    """
    _instance = None
    _initialized = False
    _init_lock = threading.Lock()
    _degraded = False
    _degraded_reason = ""

    def __new__(cls):
        # 第一重检查（无锁，性能优先）
        if cls._instance is None:
            with cls._init_lock:
                # 第二重检查（加锁后，确保线程安全）
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """
        初始化向量数据库服务 —— 双重检查锁定，只执行一次。

        初始化流程：
        1. 检查是否已初始化（第一重检查，无锁）
        2. 加锁后再次检查（第二重检查，确保线程安全）
        3. 清除 ChromaDB 缓存（避免残留 client 导致 KeyError）
        4. 创建 Chroma 实例和子服务
        5. 初始化失败：保留数据目录，标记 degraded，抛出错误（不自动删库）
        """
        if VectorStoreService._initialized:
            return

        with VectorStoreService._init_lock:
            if VectorStoreService._initialized:
                return

            persist_dir = get_abstract_path(chroma_config['persist_directory'])
            _clear_chroma_cache()

            try:
                self._init_chroma(persist_dir)
                VectorStoreService._degraded = False
                VectorStoreService._degraded_reason = ""
            except Exception as e:
                # 禁止自动删除：损坏时保留现场，便于运维备份与排查
                VectorStoreService._degraded = True
                VectorStoreService._degraded_reason = str(e)
                logger.error(
                    f"Chroma 初始化失败，已保留数据目录（不会自动删除）: {persist_dir}; error={e}"
                )
                VectorStoreService._initialized = True
                raise RuntimeError(
                    f"Chroma 初始化失败（数据目录已保留，请排查后必要时调用 reset_chroma_db_explicit）: {e}"
                ) from e

            VectorStoreService._initialized = True

    @classmethod
    def is_degraded(cls) -> bool:
        return cls._degraded

    @classmethod
    def degraded_reason(cls) -> str:
        return cls._degraded_reason

    def _init_chroma(self, persist_dir: str):
        """
        初始化 Chroma 实例和子服务。

        组件：
        - vectors_store: Chroma 向量数据库（存储文档向量）
        - md5_store: MD5 记录管理（文件去重）
        - hybrid_retriever: 混合检索器（向量 + BM25）
        - document_processor: 文档处理器（加载、分割、向量化）
        """
        self.vectors_store = Chroma(
            collection_name=chroma_config['collection_name'],
            embedding_function=embed_model,
            persist_directory=persist_dir,
        )
        self.md5_store = MD5Store()
        self.hybrid_retriever = HybridRetriever(self.vectors_store)
        self.document_processor = DocumentProcessor(self.vectors_store, self.md5_store)

    async def get_bm25_retriever(self, user_id: str = None):
        """获取 BM25 检索器 —— 透传给混合检索器"""
        return await self.hybrid_retriever.get_bm25_retriever(user_id)

    async def _delete_documents_by_md5(self, user_id: str, md5: str, label: str = ""):
        """
        删除指定 MD5 的文档向量 —— 公共方法，供 delete_by_filename 和 delete_single_md5 复用。

        :param user_id: 用户ID
        :param md5: MD5 值
        :param label: 日志标签（文件名或空）
        """
        where_clause = {"$and": [{"user_id": user_id}, {"md5": md5}]}
        await asyncio.to_thread(self.vectors_store.delete, where=where_clause)
        desc = f"文件 {label}" if label else f"MD5为 {md5}"
        logger.info(f"【向量数据库】已删除用户 {user_id} 中{desc}对应的文档")

    @staticmethod
    def _get_doc_entry(all_docs: dict, i: int) -> tuple[dict, str]:
        """
        获取第 i 个文档的 metadata 和 content —— 公共方法，消除重复的边界检查代码。

        :param all_docs: ChromaDB 查询结果
        :param i: 索引
        :return: (metadata, content) 元组
        """
        metadata = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
        content = all_docs['documents'][i] if i < len(all_docs['documents']) else ""
        return metadata, content

    async def _get_all_documents(self) -> list[Document]:
        """获取所有文档 —— 透传给混合检索器"""
        return await self.hybrid_retriever._get_all_documents()

    async def get_retriever(
        self,
        query: str = None,
        user_id: str = None,
        space_id: str = None,
        candidate_k: int | None = None,
    ):
        """获取混合检索器（向量 + BM25）——透传租户/空间过滤与候选数量。"""
        return await self.hybrid_retriever.get_retriever(
            query, user_id, space_id=space_id, candidate_k=candidate_k
        )


    @staticmethod
    async def get_dynamic_weights(query: str = None):
        """获取动态权重 —— 根据查询长度调整向量/BM25 权重"""
        return await HybridRetriever.get_dynamic_weights(query)

    async def check_md5_hex(self, md5_for_check: str, user_id: str = None) -> bool:
        """检查文件是否已上传（通过 MD5 去重）"""
        return await self.md5_store.check_md5_hex(md5_for_check, user_id)

    async def save_md5_hex(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        """保存 MD5 记录（异步版本）"""
        await self.md5_store.save_md5_hex(md5_hex, filename, original_filename, user_id)

    def save_md5_hex_sync(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        """保存 MD5 记录（同步版本，用于多线程场景）"""
        self.md5_store.save_md5_hex_sync(md5_hex, filename, original_filename, user_id)

    async def delete_user_documents(self, user_id: str):
        """
        删除用户的所有文档 —— 级联删除向量和 MD5 记录。

        删除范围：
        1. ChromaDB 中该用户的所有文档向量
        2. MD5 记录
        3. 磁盘上的 PDF 提取图片

        :param user_id: 用户ID
        """
        try:
            await self.delete_user_md5(user_id, delete_documents=True)
        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的文档时出错: {e}")
            raise

    async def delete_user_md5(self, user_id: str, delete_documents: bool = True):
        """
        删除用户的 MD5 记录 —— 可选是否同时删除向量文档。

        :param user_id: 用户ID
        :param delete_documents: 是否同时删除向量文档（默认True）
        """
        try:
            if delete_documents:
                await asyncio.to_thread(
                    self.vectors_store.delete,
                    where={"user_id": user_id}
                )
                logger.info(f"【向量数据库】已删除用户 {user_id} 的所有文档")

            await self.md5_store.delete_user_md5(user_id)
            # 同步清理该用户在磁盘上存储的所有 PDF 提取图片
            # 删除文档时必须连带删除对应的图片资源，否则会留下无法被引用的"脏"文件
            delete_user_all_images(user_id)
        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的MD5记录时出错: {e}")

    async def delete_by_filename(self, user_id: str, filename: str, delete_documents: bool = True):
        """
        按文件名删除 —— 通过文件名找到 MD5，然后删除对应数据。

        :param user_id: 用户ID
        :param filename: 文件名
        :param delete_documents: 是否同时删除向量文档
        :return: 是否成功删除
        """
        try:
            md5_to_delete = await self.md5_store.delete_by_filename(user_id, filename)
            if md5_to_delete is None:
                logger.warning(f"【向量数据库】文件 {filename} 不存在于用户 {user_id} 的MD5记录中")
                return False

            logger.info(f"【向量数据库】已删除用户 {user_id} 的文件 {filename} 的MD5记录")

            if delete_documents:
                await self._delete_documents_by_md5(user_id, md5_to_delete, filename)

            # 删除该文档对应的 PDF 提取图片目录
            delete_image_directory(user_id, md5_to_delete)

            return True

        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的文件 {filename} 时出错: {e}")
            return False

    async def delete_single_md5(self, user_id: str, md5_to_delete: str, delete_documents: bool = True):
        """
        按 MD5 值删除 —— 直接通过 MD5 删除数据。

        :param user_id: 用户ID
        :param md5_to_delete: MD5 值
        :param delete_documents: 是否同时删除向量文档
        :return: 是否成功删除
        """
        try:
            success = await self.md5_store.delete_single_md5(user_id, md5_to_delete)
            if not success:
                logger.warning(f"【向量数据库】MD5记录 {md5_to_delete} 不存在")
                return False

            logger.info(f"【向量数据库】已删除用户 {user_id} 的MD5记录: {md5_to_delete}")

            if delete_documents:
                await self._delete_documents_by_md5(user_id, md5_to_delete)

            # 清理磁盘上该用户的 PDF 提取图片
            delete_image_directory(user_id, md5_to_delete)

            return True

        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的MD5记录 {md5_to_delete} 时出错: {e}")
            return False

    async def get_md5_info(self, user_id: str, md5_value: str):
        """
        获取 MD5 信息 —— 返回文件名、上传时间等元数据。

        :param user_id: 用户ID
        :param md5_value: MD5 值
        :return: MD5 信息字典，不存在返回 None
        """
        try:
            return await self.md5_store.get_md5_info(user_id, md5_value)
        except Exception as e:
            logger.error(f"【向量数据库】获取MD5信息 {md5_value} 时出错: {e}")
            return None

    async def get_all_md5_records(self, user_id: str):
        """
        获取用户的所有 MD5 记录 —— 用于知识库列表展示。

        :param user_id: 用户ID
        :return: MD5 记录列表
        """
        try:
            records = await self.md5_store.get_all_md5_records(user_id)
            logger.info(f"【向量数据库】获取用户 {user_id} 的MD5记录，共 {len(records)} 条")
            return records
        except Exception as e:
            logger.error(f"【向量数据库】获取用户 {user_id} 的MD5记录时出错: {e}")
            return []

    async def get_user_documents(self, user_id: str = None, space_id: str = None):
        """
        获取用户的知识库文档列表 —— 按文件名聚合。

        聚合逻辑：
        1. 从 ChromaDB 获取该用户的所有文档
        2. 按 original_filename（原始文件名）分组
        3. 统计每个文件的切片数量、图片数量、内容预览
        4. 支持按 space_id 筛选

        :param user_id: 用户ID（None 则获取所有）
        :param space_id: 空间ID（None 则不筛选）
        :return: 文档信息列表
        """
        try:
            if user_id and space_id:
                where_clause = {"$and": [{"user_id": user_id}, {"space_id": space_id}]}
            elif user_id:
                where_clause = {"user_id": user_id}
            elif space_id:
                where_clause = {"space_id": space_id}
            else:
                where_clause = None
            all_docs = await asyncio.to_thread(
                self.vectors_store.get,
                include=['documents', 'metadatas'],
                where=where_clause,
            )

            docs_info = {}

            for i, doc_id in enumerate(all_docs['ids']):
                metadata, content = self._get_doc_entry(all_docs, i)

                # space_id 筛选：非空时过滤
                if space_id and metadata.get('space_id', '') != space_id:
                    continue

                # 优先使用 metadata 中保存的 original_filename（用户上传时的原始文件名）
                # 因为 source 可能存的是临时文件的完整路径（如 C:\Users\...\tmp123.pdf），
                # 而 original_filename 才是用户看到的文件名
                source = metadata.get('source', metadata.get('filename', 'unknown'))
                if isinstance(source, str) and '\\' in source:
                    source = os.path.basename(source)
                filename = metadata.get('original_filename', source)

                original_filename = metadata.get('original_filename', filename)
                if filename not in docs_info:
                    docs_info[filename] = {
                        'id': doc_id,
                        'filename': filename,
                        'original_filename': original_filename,
                        'user_id': metadata.get('user_id'),
                        'space_id': metadata.get('space_id', ''),
                        'chunk_count': 0,
                        'image_count': 0,
                        'preview': "",
                        'created_at': metadata.get('created_at')
                    }

                docs_info[filename]['chunk_count'] += 1

                # 统计图片数量：从 metadata 的 image_paths 字段提取
                image_paths = metadata.get('image_paths', [])
                if isinstance(image_paths, list):
                    docs_info[filename]['image_count'] += len(image_paths)

                if not docs_info[filename]['preview'] and content:
                    preview_length = 100
                    docs_info[filename]['preview'] = content[:preview_length] + ("..." if len(content) > preview_length else "")

            result = list(docs_info.values())
            logger.info(f"【向量数据库】获取用户 {user_id} 的知识库文档，共 {len(result)} 个文件")
            return result

        except Exception as e:
            logger.error(f"【向量数据库】获取用户 {user_id} 的知识库文档时出错: {e}")
            raise

    async def get_document_detail(self, user_id: str, filename: str):
        """
        获取文档详情 —— 返回完整内容、图片列表、切片详情。

        返回结构：
        - id: 文档ID
        - filename: 文件名
        - chunk_count: 切片数量
        - content: 完整内容（所有切片拼接）
        - images: 图片URL列表
        - chunks: 切片详情（每段文本 + 对应图片）

        :param user_id: 用户ID
        :param filename: 文件名
        :return: 文档详情字典
        """
        try:
            where_clause = {"user_id": user_id}
            all_docs = await asyncio.to_thread(
                self.vectors_store.get,
                include=['documents', 'metadatas'],
                where=where_clause
            )

            doc_info = None
            full_content = []
            chunk_count = 0
            all_images = set()
            doc_md5 = None
            chunks = []

            for i, doc_id in enumerate(all_docs['ids']):
                metadata, content = self._get_doc_entry(all_docs, i)

                source = metadata.get('source', metadata.get('filename', ''))
                if isinstance(source, str):
                    source_name = os.path.basename(source)
                else:
                    source_name = str(source)
                original_filename = metadata.get('original_filename', '')

                # 同时匹配 source 和 original_filename，兼容不同切片方式写入的 metadata
                if source_name == filename or original_filename == filename:
                    if not doc_info:
                        doc_info = {
                            'id': doc_id,
                            'filename': filename,
                            'user_id': metadata.get('user_id'),
                            'chunk_count': 0,
                            'content': "",
                            'images': [],
                            'md5': metadata.get('md5'),
                            'created_at': metadata.get('created_at')
                        }
                        doc_md5 = metadata.get('md5')
                    chunk_count += 1
                    full_content.append(content)

                    # 从 metadata 中取出该 chunk 关联的图片文件名列表，
                    # 拼接成可供前端直接请求的 URL 路径（由 knowledge_router 中的图片路由处理）
                    image_paths = metadata.get('image_paths', [])
                    chunk_images = []
                    if isinstance(image_paths, list):
                        for img_name in image_paths:
                            img_url = f"/knowledge/image/{doc_md5}/{img_name}"
                            all_images.add(img_url)
                            chunk_images.append(img_url)

                    chunks.append({
                        'chunk_id': doc_id,
                        'index': len(chunks),
                        'content': content,
                        'page': metadata.get('page'),
                        'images': chunk_images,
                    })

            if doc_info:
                doc_info['chunk_count'] = chunk_count
                doc_info['content'] = '\n'.join(full_content)
                doc_info['images'] = sorted(all_images)
                doc_info['chunks'] = chunks

            logger.info(f"【向量数据库】获取文档详情: {filename}，chunk数量: {chunk_count}，图片数量: {len(all_images)}")
            return doc_info

        except Exception as e:
            logger.error(f"【向量数据库】获取文档详情 {filename} 时出错: {e}")
            raise

    async def get_document_chunks(self, user_id: str, filename: str):
        """
        获取文档切片详情 —— 用于前端"查看切片"页面。

        匹配策略（支持模糊匹配）：
        1. 精确匹配（source == filename）
        2. 扩展名无关匹配（去掉 .pdf 后比较）
        3. 子串匹配（filename 包含在 source 中）
        4. 所有值包含匹配（兜底）

        :param user_id: 用户ID
        :param filename: 文件名
        :return: {"filename": "...", "total_chunks": N, "chunks": [...]}
        """
        try:
            where_clause = {"user_id": user_id}
            all_docs = await asyncio.to_thread(
                self.vectors_store.get,
                include=['documents', 'metadatas'],
                where=where_clause
            )

            logger.info(f"【向量数据库】get_document_chunks: user_id={user_id}, "
                        f"查询 filename={filename}, ChromaDB返回 {len(all_docs['ids'])} 个文档")

            # 调试：打印前3个文档的metadata，帮助排查匹配问题
            for i in range(min(3, len(all_docs['ids']))):
                meta = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
                logger.info(f"【向量数据库】文档[{i}] metadata keys={list(meta.keys())}, "
                            f"source={meta.get('source', 'N/A')}, "
                            f"original_filename={meta.get('original_filename', 'N/A')}, "
                            f"user_id={meta.get('user_id', 'N/A')}")

            chunks = []
            chunk_index = 0
            filename_lower = filename.lower()
            # 去掉扩展名用于模糊匹配
            filename_stem = os.path.splitext(filename)[0].lower()

            for i, doc_id in enumerate(all_docs['ids']):
                metadata, content = self._get_doc_entry(all_docs, i)

                # 从多个可能的 key 中提取文件名信息
                source = metadata.get('source', metadata.get('filename', ''))
                if isinstance(source, str):
                    source_name = os.path.basename(source)
                    source_stem = os.path.splitext(source_name)[0].lower()
                else:
                    source_name = str(source)
                    source_stem = source_name.lower()
                original_filename = metadata.get('original_filename', '')
                original_stem = os.path.splitext(original_filename)[0].lower() if original_filename else ''

                # 匹配策略：精确匹配 > 扩展名无关匹配 > 子串匹配 > 所有值包含匹配
                matched = False
                if source_name == filename or original_filename == filename:
                    matched = True
                elif filename_lower in source_name.lower() or filename_lower in original_filename.lower():
                    matched = True
                elif filename_stem and (filename_stem in source_stem or filename_stem in original_stem):
                    matched = True
                else:
                    # 最后兜底：检查 metadata 中所有字符串值是否包含文件名
                    for key, val in metadata.items():
                        if isinstance(val, str) and filename_lower in val.lower():
                            matched = True
                            break

                if matched:
                    doc_md5 = metadata.get('md5', '')
                    image_paths = metadata.get('image_paths', [])
                    if isinstance(image_paths, list):
                        images = [f"/knowledge/image/{doc_md5}/{img}" for img in image_paths]
                    else:
                        images = []

                    chunks.append({
                        'chunk_id': doc_id,
                        'index': chunk_index,
                        'content': content,
                        'metadata': metadata,
                        'images': images,
                    })
                    chunk_index += 1

            result = {
                'filename': filename,
                'total_chunks': len(chunks),
                'chunks': chunks
            }

            if len(chunks) == 0 and len(all_docs['ids']) > 0:
                logger.warning(f"【向量数据库】文档切片匹配失败: filename={filename}, "
                               f"用户共有 {len(all_docs['ids'])} 个文档但无一匹配")

            logger.info(f"【向量数据库】获取文档切片: {filename}，共 {len(chunks)} 个切片")
            return result

        except Exception as e:
            logger.error(f"【向量数据库】获取文档切片 {filename} 时出错: {e}")
            raise

    # ========== 以下方法透传给 DocumentProcessor ==========

    async def get_file_document(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        """加载文件文档 —— 透传给 DocumentProcessor（支持多模态 PDF）"""
        return await self.document_processor.get_file_document(read_path, md5, user_id)

    def get_file_document_sync(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        """加载文件文档（同步版本）—— 用于多线程场景"""
        return self.document_processor.get_file_document_sync(read_path, md5, user_id)

    def split_documents_sync(self, documents: list[Document]) -> list[Document]:
        """分割文档（同步版本）—— 用于多线程场景"""
        return self.document_processor.split_documents_sync(documents)

    async def get_document(self, files: list = None, user_id: str = None, progress_callback=None, space_id: str = ""):
        """处理文档 —— 加载、分割、向量化、存储"""
        await self.document_processor.get_document(files, user_id, progress_callback, space_id=space_id)