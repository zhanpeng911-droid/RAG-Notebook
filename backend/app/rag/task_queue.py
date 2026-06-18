import queue
import threading
from typing import Any, Optional


class TaskQueue:
    """线程安全的任务队列管理器。

    用于协调多线程切片和单线程写入之间的数据传递。在文件上传流程中，
    多个工作线程并发对文件进行切片，切片结果通过此队列传递给主线程
    顺序写入向量数据库，避免并发写入冲突。

    内部使用 Python 标准库 queue.Queue 实现线程安全的 FIFO 队列，
    并通过 threading.Lock 保护计数器等共享状态。

    属性:
        _queue: 底层线程安全队列实例。
        _completed_count: 已完成的任务计数。
        _total_count: 总任务数，由调用方通过 set_total_count 设置。
        _lock: 保护共享状态的线程锁。
        _finished: 切片阶段是否已全部完成的标志位。
    """

    def __init__(self, maxsize: int = 100):
        """初始化任务队列。

        :param maxsize: 队列最大容量，超过时 put 操作会阻塞，防止内存无限增长。
        """
        self._queue = queue.Queue(maxsize=maxsize)
        self._completed_count = 0
        self._total_count = 0
        self._lock = threading.Lock()
        self._finished = False

    def set_total_count(self, count: int):
        """设置总任务数，用于进度计算和完成判断。

        :param count: 待处理的文件总数。
        """
        with self._lock:
            self._total_count = count

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None):
        """向队列中放入一个任务结果。

        当队列已满时，若 block=True 会阻塞等待直到有空闲位置。

        :param item: 任务结果数据（通常为 SliceResult 实例）。
        :param block: 是否阻塞等待空闲位置，默认 True。
        :param timeout: 阻塞等待的超时时间（秒），None 表示无限等待。
        """
        self._queue.put(item, block=block, timeout=timeout)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Any:
        """从队列中获取一个任务结果。

        主线程调用此方法依次取出切片结果并写入向量数据库。

        :param block: 是否阻塞等待新数据，默认 True。
        :param timeout: 阻塞等待的超时时间（秒），None 表示无限等待。
        :return: 任务结果数据（SliceResult 实例）。
        """
        return self._queue.get(block=block, timeout=timeout)

    def task_done(self):
        """标记一个任务已处理完成。

        同时递增已完成计数器并通知 queue.Queue 的 join() 方法。
        每次 get() 取出数据并处理完毕后必须调用此方法。
        """
        with self._lock:
            self._completed_count += 1
        self._queue.task_done()

    def get_completed_count(self) -> int:
        """获取已完成任务数。

        :return: 已处理完成的任务数量。
        """
        with self._lock:
            return self._completed_count

    def get_total_count(self) -> int:
        """获取总任务数。

        :return: 通过 set_total_count 设置的总任务数。
        """
        with self._lock:
            return self._total_count

    def is_finished(self) -> bool:
        """判断是否所有任务都已完成。

        仅当切片阶段标志位为 True 且已完成数达到总数时返回 True。

        :return: 所有任务是否已完成。
        """
        with self._lock:
            return self._finished and self._completed_count >= self._total_count

    def set_finished(self):
        """标记切片阶段已完成。

        在所有文件切片任务提交后调用，通知消费者线程不再有新数据入队。
        """
        with self._lock:
            self._finished = True

    def join(self):
        """阻塞直到所有已入队的任务都被处理完成。

        底层调用 queue.Queue.join()，依赖 task_done() 的调用来判断完成状态。
        """
        self._queue.join()

    def qsize(self) -> int:
        """获取队列当前待处理的任务数量。

        :return: 队列中尚未被 get() 取出的任务数。
        """
        return self._queue.qsize()

    def empty(self) -> bool:
        """判断队列是否为空。

        :return: 队列中无待处理任务返回 True，否则返回 False。
        """
        return self._queue.empty()

    def full(self) -> bool:
        """判断队列是否已满。

        :return: 队列已达到 maxsize 容量返回 True，否则返回 False。
        """
        return self._queue.full()