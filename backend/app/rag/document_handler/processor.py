"""
文档处理器 —— 负责文档加载、分割、向量化。

支持的文件格式：
- TXT: 纯文本
- PDF: 支持多模态（提取图片 + 视觉描述）
- MD: Markdown
- PPTX: PowerPoint
- DOCX: Word

处理流程：
1. 读取文件
2. 根据文件类型选择加载器
3. 分割文档（保持语义完整性）
4. 生成向量嵌入
5. 存入 ChromaDB
"""
import asyncio
import os
import tempfile

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.rag.text_spliter import AsyncTextSplitter
from app.utils.config import chroma_config
from app.utils.factory import embed_model
from app.utils.file_handler import pdf_loader, txt_loader, listdir_allowed_type, get_file_md5_hex, markdown_loader, \
    ppt_loader, word_loader, pdf_loader_sync, txt_loader_sync, markdown_loader_sync, ppt_loader_sync, word_loader_sync
from app.utils.pdf_multimodal_loader import pdf_multimodal_loader, pdf_multimodal_loader_sync
from app.core.logger_handler import logger

# 扩展名 → (异步加载器, 同步加载器) 映射表
_LOADER_MAP: dict[str, tuple] = {
    '.txt': (txt_loader, txt_loader_sync),
    '.md': (markdown_loader, markdown_loader_sync),
    '.pptx': (ppt_loader, ppt_loader_sync),
    '.docx': (word_loader, word_loader_sync),
}


class DocumentProcessor:
    """文档处理器 —— 统一管理多格式文档的加载、分割、向量化"""

    def __init__(self, vectors_store: Chroma, md5_store):
        self.vectors_store = vectors_store
        self.md5_store = md5_store
        self.spliter = AsyncTextSplitter(
            chunk_size=chroma_config.get('chunk_size', 500),
            chunk_overlap=chroma_config.get('chunk_overlap', 60),
            separators=chroma_config.get('separators'),
            embedding_model=embed_model
        )

    def _get_ext(self, read_path: str) -> str:
        """获取文件小写扩展名"""
        return os.path.splitext(read_path)[1].lower()


    @staticmethod
    def resolve_chunk_params(filename: str | None = None) -> tuple[int, int]:
        """按扩展名覆盖 chunk 参数，缺省回退 chroma.yaml 全局默认。"""
        base_size = int(chroma_config.get("chunk_size", 500))
        base_overlap = int(chroma_config.get("chunk_overlap", 60))
        by_ext = chroma_config.get("chunk_by_extension") or {}
        if not filename:
            return base_size, base_overlap
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        conf = by_ext.get(ext) or {}
        return int(conf.get("chunk_size", base_size)), int(conf.get("chunk_overlap", base_overlap))

    async def get_file_document(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        """
        异步加载文件 —— 根据扩展名分发到对应加载器。

        PDF 特殊处理：提供 md5 和 user_id 时启用多模态加载（提取图片+视觉描述）。
        """
        ext = self._get_ext(read_path)

        # PDF 多模态加载（有条件分支，单独处理）
        if ext == '.pdf':
            if md5 and user_id:
                try:
                    return await pdf_multimodal_loader(read_path, md5, user_id)
                except RuntimeError as exc:
                    logger.warning(f"?PDF?????????????????????: {exc}")
            return await pdf_loader(read_path)

        # 其他格式通过字典映射
        loaders = _LOADER_MAP.get(ext)
        return await loaders[0](read_path) if loaders else []

    def get_file_document_sync(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        """
        同步加载文件（用于多线程场景）—— 根据扩展名分发到对应加载器。

        PDF 特殊处理：提供 md5 和 user_id 时启用多模态加载。
        """
        ext = self._get_ext(read_path)

        if ext == '.pdf':
            if md5 and user_id:
                try:
                    return pdf_multimodal_loader_sync(read_path, md5, user_id)
                except RuntimeError as exc:
                    logger.warning(f"?PDF????????????????????????: {exc}")
            return pdf_loader_sync(read_path)

        loaders = _LOADER_MAP.get(ext)
        return loaders[1](read_path) if loaders else []

    def split_documents_sync(self, documents: list[Document]) -> list[Document]:
        """同步分割文档（用于多线程场景）"""
        return self.spliter.split_documents_sync(documents)

    async def get_document(self, files: list = None, user_id: str = None, progress_callback=None, space_id: str = ""):
        """
        处理文档并将其转为向量存入向量数据库
        :param files: 上传的文件列表，如果为None则从数据文件夹读取
        :param user_id: 用户ID，用于标记文档的所有者
        :param progress_callback: 进度回调函数，用于实时返回处理进度
        :param space_id: 空间ID，用于归属哪个空间
        """
        file_paths = []
        file_names = {}

        if files:
            for file in files:
                filename_str = file.filename.decode() if isinstance(file.filename, bytes) else (file.filename or '')
                suffix = os.path.splitext(filename_str)[1]
                temp_file_path = await asyncio.to_thread(
                    tempfile.NamedTemporaryFile,
                    delete=False,
                    mode='w+b',
                    suffix=suffix
                )
                content = await file.read()
                await asyncio.to_thread(temp_file_path.write, content)
                file_paths.append(temp_file_path.name)
                file_names[temp_file_path.name] = file.filename
        else:
            allowed_file_path: tuple[str] = await listdir_allowed_type(
                chroma_config['data_path'],
                tuple(chroma_config['allow_knowledge_file_types'])
            )
            file_paths = list(allowed_file_path)

        for idx, file_path in enumerate(file_paths):
            filename = file_names.get(file_path, os.path.basename(file_path))

            md5_hex = await get_file_md5_hex(file_path)
            if await self.md5_store.check_md5_hex(md5_hex, user_id):
                if progress_callback:
                    await progress_callback({
                        'step': 'skipping',
                        'filename': filename,
                        'message': f'文件 {filename} 已存在，跳过'
                    })
                logger.info(f"【向量数据库】文件 {file_path} 的md5值 {md5_hex} 已存在，跳过")
                if files:
                    try:
                        os.unlink(file_path)
                    except:
                        pass
                continue

            try:
                if progress_callback:
                    await progress_callback({
                        'step': 'loading',
                        'filename': filename,
                        'message': f'正在加载文档 {filename}...'
                    })
                logger.info(f"【向量数据库】开始加载文档: {filename}")

                # 传入 md5_hex 和 user_id 以支持多模态PDF加载（图片提取和存储路径定位）
                document: list[Document] = await self.get_file_document(file_path, md5_hex, user_id)
                if not document:
                    if progress_callback:
                        await progress_callback({
                            'step': 'error',
                            'filename': filename,
                            'message': f'文件 {filename} 加载内容为空，跳过',
                            'error_message': '文件内容为空'
                        })
                    logger.error(f"【向量数据库】文件 {file_path} 加载内容为空，跳过")
                    if files:
                        try:
                            os.unlink(file_path)
                        except Exception as e:
                            pass
                    continue

                if progress_callback:
                    await progress_callback({
                        'step': 'splitting',
                        'filename': filename,
                        'message': f'正在切分文档 {filename}...'
                    })
                logger.info(f"【向量数据库】开始切分文档: {filename}")

                document: list[Document] = await self.spliter.split_documents(document)
                if not document:
                    if progress_callback:
                        await progress_callback({
                            'step': 'error',
                            'filename': filename,
                            'message': f'文件 {filename} 切分内容为空，跳过',
                            'error_message': '文档切分后为空'
                        })
                    logger.error(f"【向量数据库】文件 {file_path} 切分内容为空，跳过")
                    if files:
                        try:
                            os.unlink(file_path)
                        except:
                            pass
                    continue

                if progress_callback:
                    await progress_callback({
                        'step': 'storing',
                        'filename': filename,
                        'message': f'正在存储向量 {filename}...'
                    })
                logger.info(f"【向量数据库】开始存储向量: {filename}，文档数量: {len(document)}")

                if user_id:
                    for doc in document:
                        doc.metadata['user_id'] = user_id

                for doc in document:
                    doc.metadata['original_filename'] = filename
                    doc.metadata['md5'] = md5_hex
                    doc.metadata['space_id'] = space_id

                await asyncio.to_thread(self.vectors_store.add_documents, document)

                original_filename = file_names.get(file_path, filename) if files else filename
                await self.md5_store.save_md5_hex(md5_hex, filename, original_filename, user_id)

                if progress_callback:
                    await progress_callback({
                        'step': 'completed',
                        'filename': filename,
                        'message': f'文件 {filename} 处理完成'
                    })
                logger.info(f"【向量数据库】文件 {file_path} 的md5值 {md5_hex} 已保存")

                if files:
                    try:
                        os.unlink(file_path)
                    except:
                        pass

            except Exception as e:
                if progress_callback:
                    await progress_callback({
                        'step': 'error',
                        'filename': filename,
                        'message': f'文件 {filename} 处理失败',
                        'error_message': str(e)
                    })
                logger.error(f"【向量数据库】文件 {file_path} 处理时出错: {e}")
                if files:
                    try:
                        os.unlink(file_path)
                    except:
                        pass
                continue