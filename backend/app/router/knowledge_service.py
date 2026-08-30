import asyncio
import base64
import time
import os
import tempfile
from typing import List, AsyncGenerator
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException, UploadFile

from app.core.logger_handler import logger
from app.core.exceptions import KnowledgeException
from app.rag.vector_store import VectorStoreService
from app.rag.task_queue import TaskQueue
from app.rag.sse_models import SliceResult
from app.utils.file_handler import get_file_md5_hex_sync
from app.services.knowledge_file_validator import (
    safe_filename, detect_file_type, validate_file_type,
    validate_single_file_size, validate_total_size,
    ALLOWED_EXTENSIONS, ALLOWED_MIME_TYPES, MAX_FOLDER_SIZE,
)
from app.services import knowledge_sse_events as sse
from app.services.knowledge_record_service import KnowledgeRecordService


@dataclass
class ProcessingState:
    """
    文件上传处理过程中的状态跟踪器。

    在多文件上传流程中，该数据类用于记录各阶段的计数信息（总文件数、
    切片数、写入数、成功/失败数），并提供统一的进度计算方法。

    属性:
        total_files (int): 上传的文件总数（含验证失败的）。
        total_valid (int): 通过验证的有效文件数。
        sliced_count (int): 已完成切片的文件数。
        written_count (int): 已尝试写入向量库的文件数。
        success_count (int): 切片和写入均成功的文件数。
        failed_count (int): 处理失败的文件数（切片失败或写入失败）。
        slice_success_count (int): 切片阶段成功的文件数（可能在写入阶段失败）。
    """
    total_files: int = 0
    total_valid: int = 0
    sliced_count: int = 0
    written_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    slice_success_count: int = 0

    def current_progress(self) -> int:
        """
        计算当前整体处理进度百分比（0~99）。

        进度由两部分加权组成：
        - 切片阶段占 60% 权重（sliced_count / total_valid * 60）
        - 写入阶段占 40% 权重（written_count / total_valid * 40）

        返回:
            int: 0 到 99 的整数进度值。99 留给最终 finish 事件使用，
                 避免在处理过程中提前显示 100%。
        """
        if self.total_valid == 0:
            return 0
        slice_progress = (self.sliced_count / self.total_valid) * 60
        write_progress = (self.written_count / self.total_valid) * 40
        return int(min(99, slice_progress + write_progress))


def _sync_slice_file(file_content: bytes, filename: str, file_index: int, user_id: str, queue: TaskQueue,
                     space_id: str = ""):
    """
    在线程池中执行的同步文件切片函数。

    将上传的文件内容写入临时文件，计算 MD5，加载文档并进行文本切片，
    最后将切片结果放入任务队列供异步消费者处理。

    参数:
        file_content (bytes): 文件的原始字节内容。
        filename (str): 原始文件名，用于日志和元数据。
        file_index (int): 文件在上传列表中的序号（从 1 开始）。
        user_id (str): 上传用户的 ID。
        queue (TaskQueue): 用于传递切片结果的线程安全队列。
        space_id (str): 目标知识库空间 ID，默认为空字符串。

    关键逻辑:
        1. 写入临时文件后计算 MD5（多模态 PDF 加载器依赖 MD5 确定图片存储路径）。
        2. 通过 VectorStoreService 加载文档并切片。
        3. 为每个文档注入 user_id、original_filename、md5、space_id 元数据。
        4. 切片完成后将结果（成功/失败）放入队列。
        5. 无论成功或异常，最终都会清理临时文件。
    """
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name

        try:
            # 在加载文档之前计算 md5，因为多模态PDF加载器需要 md5 来确定图片的存储路径。
            # 如果后移（等切片完再算），多模态加载器就无法将图片保存到正确的位置。
            md5_hex = get_file_md5_hex_sync(temp_file_path)
            store = VectorStoreService()
            documents = store.get_file_document_sync(temp_file_path, md5=md5_hex, user_id=user_id)
            if not documents:
                queue.put(SliceResult.error_result(file_index=file_index, filename=filename, error="文件加载为空"))
                return

            # 在切片前写入元数据，确保切片继承
            for doc in documents:
                doc.metadata['user_id'] = user_id
                doc.metadata['original_filename'] = filename
                doc.metadata['md5'] = md5_hex
                doc.metadata['space_id'] = space_id

            split_docs = store.split_documents_sync(documents)
            if not split_docs:
                queue.put(SliceResult.error_result(file_index=file_index, filename=filename, error="切片结果为空"))
                return

            queue.put(SliceResult.success_result(
                file_index=file_index, filename=filename, documents=split_docs, md5=md5_hex
            ))
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
    except Exception as e:
        logger.error(f"【SSE上传】切片文件 {filename} 时出错: {e}")
        queue.put(SliceResult.error_result(file_index=file_index, filename=filename, error=str(e)))


class KnowledgeService:
    """
    知识库管理服务。

    负责知识库文档的上传、切片、向量化存储、查询和删除等全流程操作。
    支持单文件和多文件上传，提供同步接口和 SSE 流式进度接口两种模式。

    主要功能:
        - 单文件/多文件向量上传（handle_add_vector_single / handle_add_vector_multiple）
        - 多文件流式上传，带实时进度推送（handle_add_vector_multiple_stream）
        - MD5 去重记录的查询与删除（委托给 KnowledgeRecordService）
        - 知识库文档列表、详情、切片内容查询
        - 批量提取图片以 base64 形式返回
        - 用户级文档清理
    """

    def __init__(self):
        self.record_service = KnowledgeRecordService()

    @staticmethod
    def _safe_filename(file: UploadFile) -> str:
        """安全获取文件名 —— 兼容 wrapper，委托给 knowledge_file_validator"""
        return safe_filename(file)

    async def handle_add_vector_single(self, file: UploadFile, user_id: str, space_id: str = "") -> str:
        """
        处理单个文件的向量上传。

        验证文件大小和类型后，将文件内容加载并存入向量数据库。

        参数:
            file (UploadFile): FastAPI 上传的文件对象。
            user_id (str): 上传用户的 ID。
            space_id (str): 目标知识库空间 ID，默认为空字符串。

        返回:
            str: 上传成功的文件名。

        异常:
            KnowledgeException: 文件超过 20MB 或类型不支持时抛出。
        """
        store = VectorStoreService()

        size_error = validate_single_file_size(file)
        if size_error:
            raise KnowledgeException(message=size_error, code=400)

        content = await file.read()
        await file.seek(0)

        filename = self._safe_filename(file)

        type_error = validate_file_type(content, filename)
        if type_error:
            raise KnowledgeException(code=400, message=type_error)

        await store.get_document(files=[file], user_id=user_id, space_id=space_id)
        return filename

    async def handle_add_vector_multiple(self, files: List[UploadFile], user_id: str, space_id: str = "") -> List[str]:
        """
        处理多个文件的向量上传（同步模式，逐个串行处理）。

        先校验文件总大小是否超过 200MB，然后逐个调用 handle_add_vector_single 处理。
        任一文件处理失败会记录日志并重新抛出异常。

        参数:
            files (List[UploadFile]): 上传的文件列表。
            user_id (str): 上传用户的 ID。
            space_id (str): 目标知识库空间 ID，默认为空字符串。

        返回:
            List[str]: 成功处理的文件名列表。

        异常:
            HTTPException: 文件总大小超过 200MB 时抛出。
        """
        total_size = 0
        for file in files:
            total_size += file.size or 0

        size_error = validate_total_size(total_size)
        if size_error:
            raise HTTPException(status_code=400, detail=size_error)

        start_time = time.time()
        results = []
        for file in files:
            try:
                await self.handle_add_vector_single(file, user_id, space_id=space_id)
                results.append(file.filename)
            except Exception as e:
                logger.error(f"【添加向量】处理文件 {file.filename} 时出错: {e}")
                raise

        end_time = time.time()
        logger.info(f"【添加向量】耗时: {end_time - start_time:.2f}秒，处理文件数: {len(results)}")

        return results

    # ==================== SSE 事件委托 ====================

    @staticmethod
    def _yield_start_event(total_files: int) -> str:
        return sse.build_start_event(total_files)

    @staticmethod
    def _yield_size_error_event() -> str:
        return sse.build_size_error_event()

    @staticmethod
    def _yield_validation_error_event(
            current_index: int, total_files: int, filename: str,
            file_type: str, file_extension: str, failed_count: int
    ) -> str:
        return sse.build_validation_error_event(
            current_index, total_files, filename, file_type, file_extension, failed_count
        )

    @staticmethod
    def _yield_slicing_completed_event(result: SliceResult, state: ProcessingState) -> str:
        return sse.build_slicing_completed_event(result, state)

    @staticmethod
    def _yield_writing_event(result: SliceResult, state: ProcessingState) -> str:
        return sse.build_writing_event(result, state)

    @staticmethod
    def _yield_completed_event(result: SliceResult, state: ProcessingState) -> str:
        return sse.build_completed_event(result, state)

    @staticmethod
    def _yield_write_error_event(result: SliceResult, state: ProcessingState, error: str) -> str:
        return sse.build_write_error_event(result, state, error)

    @staticmethod
    def _yield_slice_error_event(result: SliceResult, state: ProcessingState) -> str:
        return sse.build_slice_error_event(result, state)

    @staticmethod
    def _yield_finish_event(start_time: float, total_files: int, success_count: int, failed_count: int) -> str:
        return sse.build_finish_event(start_time, total_files, success_count, failed_count)

    async def _validate_and_read_files(
            self, files: List[UploadFile]
    ) -> tuple[List[dict], List[str], int]:
        """
        验证上传文件并读取内容。

        分两阶段执行：
        阶段1: 读取所有文件内容并验证总大小是否超过 200MB 限制。
        阶段2: 逐一验证文件 MIME 类型是否在允许列表中。

        参数:
            files (List[UploadFile]): 上传的文件列表。

        返回:
            tuple: 三元素元组：
                - List[dict]: 有效文件列表，每项包含 content、filename、file_index。
                - List[str]: SSE 格式的错误事件列表（大小超限或类型不支持）。
                - int: 上传的文件总数。

        关键逻辑:
            - 每个文件读取后会 seek(0) 重置指针，供后续处理使用。
            - 类型验证同时检查 MIME 类型和文件扩展名，任一匹配即通过。
        """
        total_files = len(files)
        total_size = 0
        files_content = []
        error_events: List[str] = []

        for file in files:
            content = await file.read()
            files_content.append({'file': file, 'content': content})
            total_size += len(content)
            await file.seek(0)

        if total_size > MAX_FOLDER_SIZE:
            logger.error(f"【SSE上传】文件总大小超过限制，总大小: {total_size / (1024 * 1024):.2f}MB，限制: 200MB")
            return [], [self._yield_size_error_event()], total_files

        valid_files = []
        current_index = 1
        failed_count = 0

        for file_info in files_content:
            file = file_info['file']
            content = file_info['content']
            filename = self._safe_filename(file)
            file_type = detect_file_type(content, filename)
            file_extension = os.path.splitext(filename)[1].lower()

            if file_type not in ALLOWED_MIME_TYPES and file_extension not in ALLOWED_EXTENSIONS:
                failed_count += 1
                error_events.append(self._yield_validation_error_event(
                    current_index, total_files, filename,
                    file_type, file_extension, failed_count
                ))
                logger.warning(
                    f"【SSE上传】文件类型验证失败: {filename}，检测到类型: {file_type}，扩展名: {file_extension}")
            else:
                valid_files.append({
                    'content': content,
                    'filename': filename,
                    'file_index': current_index
                })
                logger.debug(f"【SSE上传】文件类型验证通过: {filename}")
            current_index += 1

        return valid_files, error_events, total_files

    def _start_slicing(
            self, valid_files: List[dict], user_id: str, space_id: str = ""
    ) -> tuple[TaskQueue, ThreadPoolExecutor, list]:
        """
        启动多线程文件切片。

        为每个有效文件创建一个切片任务，使用线程池并行执行。线程数取
        任务数和 CPU 核心数中的较小值。

        参数:
            valid_files (List[dict]): 已验证的文件信息列表，每项包含 content、filename、file_index。
            user_id (str): 上传用户的 ID。
            space_id (str): 目标知识库空间 ID，默认为空字符串。

        返回:
            tuple: 包含三个元素的元组：
                - TaskQueue: 线程安全的切片结果队列。
                - ThreadPoolExecutor: 线程池执行器，用于 shutdown。
                - list: 所有提交的 Future 对象列表。
        """
        queue = TaskQueue(maxsize=10)
        queue.set_total_count(len(valid_files))

        slice_tasks = [
            (info['content'], info['filename'], info['file_index'], user_id, queue, space_id)
            for info in valid_files
        ]

        max_workers = min(len(slice_tasks), max(1, os.cpu_count() or 1))
        logger.info(f"【SSE上传】切片阶段使用 {max_workers} 个线程")

        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = [executor.submit(_sync_slice_file, *args) for args in slice_tasks]

        return queue, executor, futures

    async def _process_slice_results(
            self, queue: TaskQueue, valid_count: int, store: VectorStoreService,
            state: ProcessingState, user_id: str
    ) -> AsyncGenerator[str, None]:
        """
        异步消费切片结果队列，将切片写入向量数据库并 yield SSE 进度事件。

        逐个从队列中取出切片结果，成功时写入向量库并保存 MD5 记录，
        失败时记录错误。每一步都 yield 对应的 SSE 事件供前端实时展示。

        参数:
            queue (TaskQueue): 切片结果的任务队列。
            valid_count (int): 需要处理的有效文件总数，用于判断是否全部消费完成。
            store (VectorStoreService): 向量数据库服务实例。
            state (ProcessingState): 处理状态跟踪器，各计数器会在此方法中更新。
            user_id (str): 上传用户的 ID。

        yield:
            str: 各阶段的 SSE 事件字符串（slicing_completed、writing、completed、error）。
        """
        while state.written_count < valid_count:
            try:
                result = queue.get(block=True, timeout=0.1)

                state.sliced_count += 1

                if result.success:
                    state.slice_success_count += 1

                    yield self._yield_slicing_completed_event(result, state)

                    try:
                        yield self._yield_writing_event(result, state)

                        await asyncio.to_thread(store.vectors_store.add_documents, result.documents)
                        await store.save_md5_hex(result.md5, result.filename, result.filename, user_id)

                        state.success_count += 1
                        state.written_count += 1

                        yield self._yield_completed_event(result, state)
                        logger.info(f"【SSE上传】文件 {result.filename} 写入完成")

                    except Exception as e:
                        state.written_count += 1
                        state.failed_count += 1
                        logger.error(f"【SSE上传】写入文件 {result.filename} 时出错: {e}")
                        yield self._yield_write_error_event(result, state, str(e))

                else:
                    state.written_count += 1
                    state.failed_count += 1
                    logger.error(f"【SSE上传】切片文件 {result.filename} 失败: {result.error}")
                    yield self._yield_slice_error_event(result, state)

                queue.task_done()

            except Exception:
                continue

    async def handle_add_vector_multiple_stream(
            self,
            files: List[UploadFile],
            user_id: str,
            space_id: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        处理多个文件上传并返回流式进度（多线程切片 + 单线程串行写入）。

        完整的 SSE 流式上传流程：
        1. 发送 start 事件通知前端文件总数
        2. 读取并验证所有文件（大小、类型）
        3. 多线程并行切片
        4. 串行消费队列，将切片写入向量库
        5. 发送 finish 事件汇总统计

        参数:
            files (List[UploadFile]): 上传的文件列表。
            user_id (str): 上传用户的 ID。
            space_id (str): 目标知识库空间 ID，默认为空字符串。

        yield:
            str: 各阶段的 SSE 事件字符串，前端可实时解析展示进度。

        关键逻辑:
            - 切片阶段使用多线程并行以提高吞吐
            - 写入阶段串行以避免向量库并发冲突
            - 通过 TaskQueue 解耦切片和写入两个阶段
        """
        total_files = len(files)
        logger.info(f"【SSE上传】开始处理文件上传，文件数量: {total_files}，用户ID: {user_id}")

        yield self._yield_start_event(total_files)

        # 文件验证
        valid_files, error_events, _ = await self._validate_and_read_files(files)
        for event in error_events:
            yield event

        if not valid_files:
            logger.info("【SSE上传】无有效文件可处理")
            return

        start_time = time.time()
        state = ProcessingState(
            total_files=total_files,
            total_valid=len(valid_files)
        )

        # 多线程切片
        queue, executor, _ = self._start_slicing(valid_files, user_id, space_id)

        # 串行消费 + 写入
        store = VectorStoreService()
        async for event in self._process_slice_results(queue, len(valid_files), store, state, user_id):
            yield event

        executor.shutdown(wait=True)

        logger.info(
            f"【SSE上传】文件处理完成，总数: {total_files}，"
            f"成功: {state.success_count}，失败: {state.failed_count}，"
            f"耗时: {round(time.time() - start_time, 2)}秒"
        )

        yield self._yield_finish_event(start_time, total_files, state.success_count, state.failed_count)

    # ==================== MD5 / 去重记录管理（委托给 KnowledgeRecordService） ====================

    async def clean_user_upload(self, user_id: str) -> None:
        """删除指定用户的所有上传向量文档"""
        await self.record_service.clean_user_upload(user_id)

    async def handle_clear_user_md5(self, user_id: str, delete_documents: bool = True) -> None:
        """清空用户的所有 MD5 去重记录"""
        await self.record_service.clear_user_md5(user_id, delete_documents)

    async def handle_delete_single_md5(self, user_id: str, md5_value: str, delete_documents: bool = True) -> bool:
        """删除用户的单条 MD5 记录"""
        return await self.record_service.delete_single_md5(user_id, md5_value, delete_documents)

    async def handle_delete_by_filename(self, user_id: str, filename: str, delete_documents: bool = True) -> bool:
        """按文件名删除用户的知识库文件"""
        return await self.record_service.delete_by_filename(user_id, filename, delete_documents)

    async def handle_get_md5_info(self, user_id: str, md5_value: str):
        """查询指定 MD5 记录的详细信息"""
        return await self.record_service.get_md5_info(user_id, md5_value)

    async def handle_get_all_md5_records(self, user_id: str):
        """获取用户的所有 MD5 去重记录列表"""
        return await self.record_service.get_all_md5_records(user_id)

    async def handle_get_user_knowledge(self, user_id: str, space_id: str = None) -> list:
        """
        获取用户的知识库文档列表。

        参数:
            user_id (str): 目标用户的 ID。
            space_id (str, optional): 按空间 ID 过滤，None 表示获取所有文档。

        返回:
            list: 文档信息字典列表，每个字典包含文件名、切片数等信息。
        """
        store = VectorStoreService()
        documents = await store.get_user_documents(user_id, space_id=space_id)
        logger.info(f"【知识库】获取用户 {user_id} 的知识库文档，共 {len(documents)} 个文件")
        return documents

    async def handle_get_document_detail(self, user_id: str, filename: str) -> dict:
        """
        获取指定文档的详细信息。

        参数:
            user_id (str): 目标用户的 ID。
            filename (str): 文档文件名。

        返回:
            dict: 文档详情字典。

        异常:
            HTTPException: 文档不存在时抛出 404 错误。
        """
        store = VectorStoreService()
        document = await store.get_document_detail(user_id, filename)
        if not document:
            raise HTTPException(status_code=404, detail=f"文档 {filename} 不存在")
        logger.info(f"【知识库】获取文档详情: {filename}")
        return document

    async def handle_get_document_chunks(self, user_id: str, filename: str) -> dict:
        """
        获取指定文档的切片内容列表。

        参数:
            user_id (str): 目标用户的 ID。
            filename (str): 文档文件名。

        返回:
            dict: 包含文件名、切片总数和切片内容列表的字典。
                 切片为空时返回空列表。
        """
        store = VectorStoreService()
        chunks = await store.get_document_chunks(user_id, filename)
        if chunks['total_chunks'] == 0:
            logger.warning(f"【知识库】文档切片为空: {filename}")
            return {"filename": filename, "total_chunks": 0, "chunks": []}
        logger.info(f"【知识库】获取文档切片: {filename}，共 {chunks['total_chunks']} 个切片")
        return chunks

    async def handle_get_batch_images(self, user_id: str, md5: str) -> dict:
        """
        批量读取某个文档的所有提取图片，以 base64 data URL 的形式返回。

        前端可一次请求拿到所有图片，然后根据 chunk 中的 image_paths 按需渲染，
        避免了每个图片单独发 HTTP 请求的性能开销（尤其适合移动端或图片较多的场景）。

        参数:
            user_id (str): 目标用户的 ID。
            md5 (str): 文档的 MD5 哈希值，用于定位图片存储目录。

        返回:
            dict: 包含 md5 和 images 字典的响应，images 的 key 为文件名，
                 value 为 base64 data URL 字符串。图片目录不存在时返回空字典。

        异常:
            HTTPException: 图片读取失败时抛出 500 错误。

        关键逻辑:
            - 图片存储路径为 data/extracted_images/{user_id}/{md5}/
            - 支持 PNG、JPEG、TIFF、BMP、GIF、WebP 等格式
            - 文件名按字母排序以保证返回顺序一致
        """
        from app.utils.path_tool import get_data_path
        from app.services.knowledge_file_validator import is_valid_md5
        if not is_valid_md5(md5):
            raise HTTPException(status_code=400, detail="非法MD5")
        image_dir = os.path.join(get_data_path(), 'extracted_images', user_id, md5)
        if not os.path.isdir(image_dir):
            logger.warning(f"【知识库】图片目录不存在: {image_dir}")
            return {"md5": md5, "images": {}}

        images = {}
        try:
            for filename in sorted(os.listdir(image_dir)):
                filepath = os.path.join(image_dir, filename)
                if not os.path.isfile(filepath):
                    continue
                _, ext = os.path.splitext(filename)
                mime_map = {
                    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.tiff': 'image/tiff', '.tif': 'image/tiff',
                    '.bmp': 'image/bmp', '.gif': 'image/gif', '.webp': 'image/webp',
                }
                mime = mime_map.get(ext.lower(), 'application/octet-stream')
                with open(filepath, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                images[filename] = f"data:{mime};base64,{b64}"
        except Exception as e:
            logger.error(f"【知识库】读取批量图片失败: {e}")
            raise HTTPException(status_code=500, detail=f"读取图片失败: {e}")

        logger.info(f"【知识库】读取批量图片: {md5}，共 {len(images)} 张")
        return {"md5": md5, "images": images}


def get_knowledge_service() -> KnowledgeService:
    """
    获取知识库服务实例（用于 FastAPI 依赖注入）。

    返回:
        KnowledgeService: 新创建的知识库服务实例。
    """
    return KnowledgeService()
