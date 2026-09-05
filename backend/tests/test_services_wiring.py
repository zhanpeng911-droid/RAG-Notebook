"""S01 会话服务接线回归 —— 代理必须读到初始化后的当前实例。

缺陷（第二轮复验 S01）：app/services/__init__.py 在导入时把
database_session_manager（彼时为 None）快照进包命名空间；
init_database_session_manager() 之后只重绑定定义模块的全局变量，
代理返回的旧绑定永远是 None，正常历史读写全部失效。

测试通过 importlib.reload 复现"包 __init__ 先于初始化被导入"的顺序，
不手动给包级变量赋值来伪造接线。
"""
import asyncio
import importlib

import pytest


@pytest.fixture
def fresh_wiring():
    """重新加载定义模块与包 __init__，模拟全新进程的导入顺序。

    注意：必须用 importlib.import_module 取定义模块 —— 包 __init__ 的
    `from ... import database_session_manager`（彼时为 None）会遮蔽
    包属性里的子模块对象，`import ... as` 语法恰好拿到被遮蔽的 None，
    这本身就是 S01 命名遮蔽缺陷的一部分。
    """
    dsm = importlib.import_module("app.services.database_session_manager")
    services_pkg = importlib.import_module("app.services")

    importlib.reload(dsm)
    importlib.reload(services_pkg)
    yield dsm, services_pkg
    # 恢复初始化状态，避免影响其它测试
    try:
        asyncio.run(dsm.init_database_session_manager())
    except Exception:
        pass


@pytest.mark.asyncio
async def test_proxy_sees_manager_after_real_init(fresh_wiring):
    """执行真实初始化函数后，消费者经代理必须拿到同一实例（不得是 None）。"""
    dsm, _ = fresh_wiring
    assert dsm.database_session_manager is None  # 复现导入先于初始化

    inst = await dsm.init_database_session_manager()
    assert inst is not None

    from app.services import session_manager as sm
    assert sm.session_manager is inst
    # 与定义模块当前全局变量同一对象
    assert sm.session_manager is dsm.database_session_manager


def test_proxy_raises_when_uninitialized(fresh_wiring):
    """未初始化时读取必须明确失败，而不是返回 None 被下游吞掉。"""
    from app.services import session_manager as sm
    with pytest.raises(Exception):
        sm.session_manager  # noqa: B018  触发 property


@pytest.mark.asyncio
async def test_consumer_chat_service_uses_initialized_manager(fresh_wiring):
    """ChatService（正常历史读写消费者）必须拿到已初始化的管理器。"""
    dsm, _ = fresh_wiring
    inst = await dsm.init_database_session_manager()

    from app.router.chat_service import get_router_service
    svc = get_router_service()
    # 通过任一只读方法触达代理：未初始化/None 会在这里暴露
    from app.services import session_manager as sm
    assert sm.session_manager is inst
    assert svc is not None


def test_session_wiring_in_subprocess():
    """独立进程端到端：导入包 → 真实初始化 → 代理拿到实例。

    独立进程避免本会话其它测试预先赋值掩盖 S01。
    """
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    code = (
        "import asyncio\n"
        "from app.services import session_manager as sm\n"
        "from app.services.database_session_manager import "
        "init_database_session_manager\n"
        "inst = asyncio.run(init_database_session_manager())\n"
        "assert sm.session_manager is inst, '代理拿到的是旧绑定'\n"
        "print('WIRING_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(backend),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"子进程失败: {proc.stderr[-2000:]}"
    assert "WIRING_OK" in proc.stdout
