"""review_router 测试 —— 今日回顾列表 / 标记已回顾 / 到期计数。

真实 review_service + 内存 SQLite（Note + ReviewRecord），鉴权负路径全覆盖。
"""
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.config.validator import get_settings
from app.models.chat_history import Base

SECRET = get_settings().SECRET_KEY
USER_A = "u-aaaa-0000-0000-000000000001"


def _token(user_id):
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture(autouse=True)
def disable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield


@pytest_asyncio.fixture
async def client():
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.review_router import review_router

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)

    app = FastAPI()
    app.include_router(review_router, prefix="/api/v1")
    from app.core.failed_response_register import register_exception_handlers
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 种子：用户笔记 + 一条已到期的回顾记录
    from app.models.note import Note
    from app.models.review_record import ReviewRecord
    async with factory() as s:
        s.add(Note(id="n-1", user_id=USER_A, title="待回顾", content="内容",
                   category="study"))
        s.add(ReviewRecord(id="r-1", user_id=USER_A, note_id="n-1",
                           review_count=1, interval_days=1,
                           last_reviewed_at=datetime.now() - timedelta(days=2),
                           next_review_at=datetime.now() - timedelta(days=1)))
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        c._factory = factory
        yield c
    await engine.dispose()


# ---------- 鉴权负路径 ----------

@pytest.mark.asyncio
async def test_no_token_401(client):
    r = await client.get("/api/v1/review/today")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bad_token_401(client):
    r = await client.get("/api/v1/review/today",
                         headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


# ---------- 正路径 ----------

@pytest.mark.asyncio
async def test_today_reviews(client):
    r = await client.get("/api/v1/review/today", headers=_auth(USER_A))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total_count"] == 1
    assert data["reviews"][0]["title"] == "待回顾"


@pytest.mark.asyncio
async def test_due_count(client):
    r = await client.get("/api/v1/review/due-count", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["due_count"] == 1


@pytest.mark.asyncio
async def test_mark_reviewed_ok(client):
    r = await client.post("/api/v1/review/done/n-1", headers=_auth(USER_A))
    assert r.status_code == 200
    assert r.json()["data"]["review_count"] == 2


@pytest.mark.asyncio
async def test_mark_reviewed_missing_404(client):
    r = await client.post("/api/v1/review/done/n-nope", headers=_auth(USER_A))
    assert r.status_code == 404


# ---------- 直接调用端点函数 ----------

from app.router.review_router import (  # noqa: E402
    get_today_reviews, mark_reviewed, get_due_count,
)


@pytest.mark.asyncio
async def test_direct_today(client):
    async with client._factory() as db:
        resp = await get_today_reviews(USER_A, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_mark_reviewed(client):
    async with client._factory() as db:
        resp = await mark_reviewed("n-1", USER_A, db)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_direct_due_count(client):
    async with client._factory() as db:
        resp = await get_due_count(USER_A, db)
        assert resp.status_code == 200
