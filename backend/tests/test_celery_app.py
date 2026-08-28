"""celery_app 直接单测 —— 三个任务（标签生成/向量同步/向量删除）的成功与重试路径。

importlib 取真实模块（父包命名空间会被 __init__ 里的变量遮蔽）；
任务通过 Celery 实例属性访问，.run 直接调用底层函数并注入 mock self。
"""
import importlib
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.models.chat_history import Base

celery_mod = importlib.import_module("app.tasks.celery_app")
CELERY = celery_mod.celery_app

USER_A = "u-aaaa-0000-0000-000000000001"


def _patch_task_retry(monkeypatch, task_proxy):
    """把真实 Task 的 retry 替换为「记录并重新抛出」，供重试分支测试。"""
    real = task_proxy._get_current_object()
    recorded = []
    monkeypatch.setattr(real, "retry", lambda exc=None, **k: (_ for _ in ()).throw(exc) if exc else None)
    return recorded


@pytest.fixture(autouse=True)
def _stub_service(monkeypatch):
    """把 note_service 的方法替换成可编程桩。"""
    import app.services.note_service as ns
    svc = ns.note_service
    monkeypatch.setattr(svc, "_auto_tag_and_review", NS())
    monkeypatch.setattr(svc, "note_index", NS())
    monkeypatch.setattr(svc, "note_repo", NS())
    yield svc


# ---------- 配置 ----------

def test_celery_config():
    assert CELERY.conf.include == ["app.tasks.index_task"]
    assert "batch-index-pending-every-5-minutes" in CELERY.conf.beat_schedule
    assert CELERY.conf.timezone == "Asia/Shanghai"
    assert CELERY.conf.task_track_started is True
    assert CELERY.conf.task_acks_late is True


# ---------- generate_tags_task ----------

def test_generate_tags_success(monkeypatch, _stub_service):
    called = {}

    async def fake_auto(note_id, user_id, content, llm_config=None):
        called["note_id"] = note_id
        return None
    monkeypatch.setattr(_stub_service, "_auto_tag_and_review", fake_auto)

    result = celery_mod.generate_tags_task.run("n1", USER_A, "内容")
    assert result == {"status": "completed", "note_id": "n1"}
    assert called["note_id"] == "n1"


def test_generate_tags_retry(monkeypatch, _stub_service):
    async def fake_auto(note_id, user_id, content, llm_config=None):
        raise RuntimeError("LLM挂")
    monkeypatch.setattr(_stub_service, "_auto_tag_and_review", fake_auto)

    real = celery_mod.generate_tags_task._get_current_object()
    monkeypatch.setattr(real, "retry",
                        lambda exc=None, **k: (_ for _ in ()).throw(RuntimeError("retried")))
    with pytest.raises(RuntimeError):
        celery_mod.generate_tags_task.run("n1", USER_A, "内容")


# ---------- sync_note_vector_task ----------

@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=engine, class_=AsyncSession,
                             expire_on_commit=False)
    import app.db.db_config as dbc
    monkeypatch.setattr(dbc, "AsyncSessionLocal", fac)
    yield fac
    await engine.dispose()


def test_sync_vector_note_found(factory, monkeypatch, _stub_service):
    class FakeRepo:
        async def get_by_id(self, session, note_id, user_id):
            return NS(id="n1", user_id=USER_A, title="t", content="c")
    monkeypatch.setattr(_stub_service, "note_repo", FakeRepo())
    upserted = []
    monkeypatch.setattr(
        _stub_service, "note_index",
        NS(upsert_note=lambda *a, **k: upserted.append(a),
           delete_note=lambda *a, **k: None))

    celery_mod.sync_note_vector_task.run("n1", USER_A)
    assert upserted


def test_sync_vector_note_missing_deletes(factory, monkeypatch, _stub_service):
    class FakeRepo:
        async def get_by_id(self, session, note_id, user_id):
            return None
    monkeypatch.setattr(_stub_service, "note_repo", FakeRepo())
    deleted = []
    monkeypatch.setattr(
        _stub_service, "note_index",
        NS(upsert_note=lambda *a, **k: None,
           delete_note=lambda *a, **k: deleted.append(a)))

    celery_mod.sync_note_vector_task.run("n-missing", USER_A)
    assert deleted


def test_sync_vector_retry(factory, monkeypatch, _stub_service):
    monkeypatch.setattr(_stub_service, "note_repo", NS())

    def _boom(*a, **k):
        raise RuntimeError("向量写入失败")
    monkeypatch.setattr(_stub_service, "note_index",
                        NS(upsert_note=_boom, delete_note=_boom))

    real = celery_mod.sync_note_vector_task._get_current_object()
    monkeypatch.setattr(real, "retry",
                        lambda exc=None, **k: (_ for _ in ()).throw(RuntimeError("retried")))
    with pytest.raises(RuntimeError):
        celery_mod.sync_note_vector_task.run("n1", USER_A)


# ---------- delete_note_vector_task ----------

def test_delete_note_vector_success(monkeypatch, _stub_service):
    deleted = []
    monkeypatch.setattr(_stub_service, "note_index",
                        NS(delete_note=lambda *a, **k: deleted.append(a)))
    celery_mod.delete_note_vector_task.run("n1", USER_A)
    assert deleted


def test_delete_note_vector_retry(monkeypatch, _stub_service):
    def _boom(*a, **k):
        raise RuntimeError("删除失败")
    monkeypatch.setattr(_stub_service, "note_index", NS(delete_note=_boom))

    real = celery_mod.delete_note_vector_task._get_current_object()
    monkeypatch.setattr(real, "retry",
                        lambda exc=None, **k: (_ for _ in ()).throw(RuntimeError("retried")))
    with pytest.raises(RuntimeError):
        celery_mod.delete_note_vector_task.run("n1", USER_A)
