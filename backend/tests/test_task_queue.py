"""TaskQueue 单元测试 —— 线程安全队列、计数器与完成标志。"""
import queue
import threading

import pytest

from tests.helpers.unmock import restore_real

# conftest 将 app.rag.task_queue 整体 mock，删除假条目以导入真实实现
restore_real("app.rag.task_queue")

from app.rag.task_queue import TaskQueue  # noqa: E402


def test_put_get_fifo():
    q = TaskQueue()
    q.put("a")
    q.put("b")
    assert q.get() == "a"
    assert q.get() == "b"


def test_get_empty_with_timeout_raises():
    q = TaskQueue()
    with pytest.raises(queue.Empty):
        q.get(block=True, timeout=0.01)


def test_counters_and_total():
    q = TaskQueue()
    q.set_total_count(3)
    q.put("x")
    q.put("y")
    for _ in range(2):
        q.get()
        q.task_done()
    assert q.get_completed_count() == 2
    assert q.get_total_count() == 3


def test_is_finished_requires_flag_and_count():
    q = TaskQueue()
    q.set_total_count(2)
    q.set_finished()
    # 计数未达标：未完成
    assert not q.is_finished()
    q._completed_count = 2
    assert q.is_finished()
    # 标志位未设置时即使计数达标也未完成
    q2 = TaskQueue()
    q2.set_total_count(0)
    assert not q2.is_finished()


def test_set_finished_and_empty_state():
    q = TaskQueue()
    assert q.empty()
    assert not q.full()
    q.put(1)
    assert q.qsize() == 1
    assert not q.empty()


def test_maxsize_full_blocks_then_raises():
    q = TaskQueue(maxsize=1)
    q.put(1)
    assert q.full()
    with pytest.raises(queue.Full):
        q.put(2, block=True, timeout=0.01)


def test_join_returns_when_all_done():
    q = TaskQueue()
    results = []

    def consumer():
        for _ in range(5):
            item = q.get()
            results.append(item)
            q.task_done()

    t = threading.Thread(target=consumer)
    t.start()
    for i in range(5):
        q.put(i)
    q.join()
    assert sorted(results) == list(range(5))
    t.join(timeout=1)
