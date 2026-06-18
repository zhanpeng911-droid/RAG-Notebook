"""
知识库 SSE 事件构造 —— 封装上传流程中所有 SSE 事件的生成。

职责：
- 生成 start/error/slicing_completed/writing/completed/finish 等 SSE 事件
- 保持与前端 SSE 解析逻辑完全兼容（字段名、类型不变）
"""
import time

from app.rag.sse_models import SSEEvent, SliceResult


# ==================== SSE 事件工厂函数 ====================


def build_start_event(total_files: int) -> str:
    """
    生成 SSE 开始事件。

    参数:
        total_files (int): 待处理的文件总数。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'start'。
    """
    return SSEEvent(
        event_type='start', total_files=total_files, message='开始处理文件...', progress=0
    ).to_sse()


def build_size_error_event() -> str:
    """
    生成 SSE 文件总大小超限错误事件。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'error'。
    """
    return SSEEvent(
        event_type='error', message='文件总大小不能超过200MB',
        error_message='文件总大小不能超过200MB'
    ).to_sse()


def build_validation_error_event(
        current_index: int, total_files: int, filename: str,
        file_type: str, file_extension: str, failed_count: int
) -> str:
    """
    生成 SSE 单文件验证失败事件。

    参数:
        current_index (int): 当前文件在列表中的序号。
        total_files (int): 文件总数。
        filename (str): 文件名。
        file_type (str): libmagic 检测到的 MIME 类型。
        file_extension (str): 文件扩展名。
        failed_count (int): 当前累计失败文件数。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'error'。
    """
    return SSEEvent(
        event_type='error', file_index=current_index, total_files=total_files,
        filename=filename, step='validation',
        message=f'文件 {filename} 类型不支持',
        error_message=f'文件类型: {file_type}，扩展名: {file_extension}',
        progress=int(current_index / total_files * 100),
        failed_count=failed_count
    ).to_sse()


def build_slicing_completed_event(result: SliceResult, state) -> str:
    """
    生成 SSE 单文件切片完成事件。

    参数:
        result (SliceResult): 切片结果对象，包含文件名、切片数量等。
        state: 当前处理状态（需有 current_progress/success_count/failed_count/slice_success_count）。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'slicing_completed'。
    """
    return SSEEvent(
        event_type='slicing_completed', file_index=result.file_index,
        total_files=state.total_files, filename=result.filename,
        chunk_count=result.chunk_count, step='slicing',
        message=f'文件 {result.filename} 切片完成，共 {result.chunk_count} 个切片',
        progress=state.current_progress(),
        success_count=state.success_count, failed_count=state.failed_count,
        slice_success_count=state.slice_success_count
    ).to_sse()


def build_writing_event(result: SliceResult, state) -> str:
    """
    生成 SSE 开始写入向量库事件。

    参数:
        result (SliceResult): 切片结果对象。
        state: 当前处理状态。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'writing'。
    """
    return SSEEvent(
        event_type='writing', file_index=result.file_index,
        total_files=state.total_files, filename=result.filename,
        step='writing', message=f'正在写入向量 {result.filename}...',
        progress=state.current_progress(),
        success_count=state.success_count, failed_count=state.failed_count,
        slice_success_count=state.slice_success_count
    ).to_sse()


def build_completed_event(result: SliceResult, state) -> str:
    """
    生成 SSE 单文件处理完成事件（切片和写入均成功）。

    参数:
        result (SliceResult): 切片结果对象。
        state: 当前处理状态。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'completed'。
    """
    return SSEEvent(
        event_type='completed', file_index=result.file_index,
        total_files=state.total_files, filename=result.filename,
        step='completed', message=f'文件 {result.filename} 处理完成',
        progress=state.current_progress(),
        success_count=state.success_count, failed_count=state.failed_count,
        slice_success_count=state.slice_success_count
    ).to_sse()


def build_write_error_event(result: SliceResult, state, error: str) -> str:
    """
    生成 SSE 写入向量库失败事件。

    参数:
        result (SliceResult): 切片结果对象。
        state: 当前处理状态。
        error (str): 错误信息。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'error'。
    """
    return SSEEvent(
        event_type='error', file_index=result.file_index,
        total_files=state.total_files, filename=result.filename,
        step='writing', message=f'文件 {result.filename} 写入失败',
        error_message=error,
        progress=state.current_progress(),
        success_count=state.success_count, failed_count=state.failed_count,
        slice_success_count=state.slice_success_count
    ).to_sse()


def build_slice_error_event(result: SliceResult, state) -> str:
    """
    生成 SSE 切片阶段失败事件。

    参数:
        result (SliceResult): 包含错误信息的切片结果对象。
        state: 当前处理状态。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'error'。
    """
    return SSEEvent(
        event_type='error', file_index=result.file_index,
        total_files=state.total_files, filename=result.filename,
        step='slicing', message=f'文件 {result.filename} 切片失败',
        error_message=result.error,
        progress=state.current_progress(),
        success_count=state.success_count, failed_count=state.failed_count,
        slice_success_count=state.slice_success_count
    ).to_sse()


def build_finish_event(start_time: float, total_files: int, success_count: int, failed_count: int) -> str:
    """
    生成 SSE 全部处理完成事件。

    计算总耗时并汇总成功/失败数。

    参数:
        start_time (float): 处理开始时的 time.time() 时间戳。
        total_files (int): 上传的文件总数。
        success_count (int): 成功处理的文件数。
        failed_count (int): 失败的文件数。

    返回:
        str: 序列化后的 SSE 格式字符串，event_type 为 'finish'，progress 为 100。
    """
    total_time = round(time.time() - start_time, 2)
    return SSEEvent(
        event_type='finish', total_files=total_files,
        success_count=success_count, failed_count=failed_count,
        message=f'处理完成，耗时 {total_time} 秒', progress=100
    ).to_sse()
