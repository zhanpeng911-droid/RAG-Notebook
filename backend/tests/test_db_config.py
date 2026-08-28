"""db_config 直接单测 —— init_db / get_db / check_mysql_connection。

async_engine / AsyncSessionLocal monkeypatch 为假实现，避免真实 MySQL。
"""
from types import SimpleNamespace as NS

import pytest

import app.db.db_config as dbc


class _FakeConn:
    def __init__(self, boom=False):
        self.boom = boom
        self.executed = 0

    async def execute(self, stmt):
        if self.boom:
            raise RuntimeError("连接失败")
        self.executed += 1
        return NS()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, boom=False):
        self.boom = boom
        self.conns = []

    def connect(self):
        conn = _FakeConn(boom=self.boom)
        self.conns.append(conn)
        return conn


@pytest.mark.asyncio
async def test_init_db(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(dbc, "async_engine", engine)
    await dbc.init_db()
    assert engine.conns[0].executed == 1


@pytest.mark.asyncio
async def test_get_db_yields_and_closes(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.closed = False
            self.rolled = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def close(self):
            self.closed = True

        async def rollback(self):
            self.rolled = True

    session = FakeSession()
    monkeypatch.setattr(dbc, "AsyncSessionLocal", lambda: session)

    gen = dbc.get_db()
    got = await gen.__anext__()
    assert got is session
    await gen.aclose()
    assert session.closed


@pytest.mark.asyncio
async def test_get_db_rollback_on_error(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.closed = False
            self.rolled = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def close(self):
            self.closed = True

        async def rollback(self):
            self.rolled = True

    session = FakeSession()
    monkeypatch.setattr(dbc, "AsyncSessionLocal", lambda: session)

    gen = dbc.get_db()
    await gen.__anext__()
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("boom"))
    assert session.rolled
    assert session.closed


@pytest.mark.asyncio
async def test_check_mysql_ok(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(dbc, "async_engine", engine)
    assert await dbc.check_mysql_connection() is True
    assert engine.conns[0].executed == 1


@pytest.mark.asyncio
async def test_check_mysql_fail_after_retries(monkeypatch):
    engine = _FakeEngine(boom=True)
    monkeypatch.setattr(dbc, "async_engine", engine)

    assert await dbc.check_mysql_connection() is False
    assert len(engine.conns) == 3  # 3 次重试
