"""note_router 测试 —— 真实 JWT 鉴权负路径 + 内存库 CRUD 正路径。

独立 FastAPI app 挂载 note_router，override get_db 为内存 SQLite，
rate_limit 通过 RATE_LIMIT_ENABLED=false 短路；note_service 用真实实现。
"""
from types import SimpleNamespace as NS

import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.config.validator import get_settings
from app.models.chat_history import Base
from app.models.note import Note

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"
USER_B = "u-bbbb-0000-0000-000000000002"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    # env 优先于文件缓存，置 false 使 rate_limit 依赖直接放行
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client():
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.note_router import note_router

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    app = FastAPI()
    app.include_router(note_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 种子：A 一篇笔记
    async with factory() as s:
        s.add(Note(id="n-seed", user_id=USER_A, title="我的笔记",
                   content="内容甲", category="study"))
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        yield c
    await engine.dispose()


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_returns_401(client):
    r = await client.get("/api/v1/note/list")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_returns_401(client):
    r = await client.get("/api/v1/note/list",
                         headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


# ---------- 正路径 ----------

@pytest.mark.asyncio
async def test_list_notes_own_only(client):
    r = await client.get("/api/v1/note/list",
                         headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["total_count"] == 1
    assert body["data"]["notes"][0]["title"] == "我的笔记"


@pytest.mark.asyncio
async def test_create_note_persists(client):
    r = await client.post(
        "/api/v1/note/create",
        headers={"Authorization": f"Bearer {_token(USER_A)}"},
        json={"title": "新笔记", "content": "内容"},
    )
    assert r.status_code == 200
    note_id = r.json()["data"]["id"]

    r2 = await client.get(f"/api/v1/note/{note_id}",
                          headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r2.json()["data"]["title"] == "新笔记"


@pytest.mark.asyncio
async def test_cross_user_note_isolation_404(client):
    r = await client.get("/api/v1/note/n-seed",
                         headers={"Authorization": f"Bearer {_token(USER_B)}"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_note_own(client):
    r = await client.put(
        "/api/v1/note/n-seed",
        headers={"Authorization": f"Bearer {_token(USER_A)}"},
        json={"title": "改名"},
    )
    assert r.status_code == 200
    detail = await client.get(
        "/api/v1/note/n-seed",
        headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert detail.json()["data"]["title"] == "改名"


@pytest.mark.asyncio
async def test_update_cross_user_404(client):
    r = await client.put(
        "/api/v1/note/n-seed",
        headers={"Authorization": f"Bearer {_token(USER_B)}"},
        json={"title": "偷改"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_own_then_404(client):
    r = await client.delete("/api/v1/note/n-seed",
                            headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200
    r2 = await client.get("/api/v1/note/n-seed",
                          headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_stats_shape(client):
    r = await client.get("/api/v1/note/stats",
                         headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200
    assert "total" in r.json()["data"]


@pytest.mark.asyncio
async def test_invalid_query_param_422(client):
    r = await client.get("/api/v1/note/list?page=0",
                         headers={"Authorization": f"Bearer {_token(USER_A)}"})
    # 注册了 validation_exception_handler → 规范化为 400 + 友好文案
    assert r.status_code == 400
    assert "page" in r.json()["message"]


@pytest.mark.asyncio
async def test_autocomplete_requires_auth_but_service_stub(client, monkeypatch):
    from app.router.note_router import note_service as ns
    async def fake_ac(context, llm_config=None):
        return {"text": "补全文本", "success": True}
    monkeypatch.setattr(ns, "autocomplete", fake_ac)
    r = await client.post(
        "/api/v1/note/autocomplete",
        headers={"Authorization": f"Bearer {_token(USER_A)}"},
        json={"context": "写"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["text"] == "补全文本"


# ---------- 其余端点（service stub） ----------

@pytest.mark.asyncio
async def test_search_notes_endpoint(client, monkeypatch):
    from app.router.note_router import note_service as ns
    async def fake(db, user_id, q):
        return [{"id": "n1", "user_id": USER_A, "title": "命中",
                 "content": "内容", "category": None, "tags": None,
                 "created_at": None}]
    monkeypatch.setattr(ns, "search_notes", fake)
    r = await client.get("/api/v1/note/search?q=命中",
                         headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200
    assert r.json()["data"]["total_count"] == 1


@pytest.mark.asyncio
async def test_related_by_query_endpoint(client, monkeypatch):
    from app.router.note_router import note_service as ns
    async def fake(q, user_id, top_k):
        return [{"source": "note", "title": "相关"}]
    monkeypatch.setattr(ns, "search_related_notes", fake)
    r = await client.get("/api/v1/note/related?q=x",
                         headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 1


@pytest.mark.asyncio
async def test_export_note_endpoint(client):
    r = await client.get("/api/v1/note/n-seed/export",
                         headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200
    assert "markdown" in r.json()["data"]


@pytest.mark.asyncio
async def test_note_related_endpoint(client):
    r = await client.get("/api/v1/note/n-seed/related",
                         headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_auto_tag_endpoint(client, monkeypatch):
    from app.router.note_router import note_service as ns
    async def fake_get(db, nid, uid):
        return NS(id="n-seed", content="内容", title="t")
    monkeypatch.setattr(ns, "get_note", fake_get)
    import importlib
    celery_mod = importlib.import_module("app.tasks.celery_app")
    monkeypatch.setattr(celery_mod, "generate_tags_task",
                        NS(delay=lambda *a, **k: None))
    r = await client.post("/api/v1/note/n-seed/auto-tag",
                          headers={"Authorization": f"Bearer {_token(USER_A)}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_assist_stream_endpoint(client, monkeypatch):
    from app.router.note_router import note_service as ns
    async def gen(content, action, llm_config=None):
        yield "data: {\"type\": \"response\", \"content\": \"续写\"}\n\n"

    monkeypatch.setattr(ns, "assist_stream", gen)
    r = await client.post(
        "/api/v1/note/assist/stream",
        headers={"Authorization": f"Bearer {_token(USER_A)}"},
        json={"content": "正文", "action": "continue"},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
