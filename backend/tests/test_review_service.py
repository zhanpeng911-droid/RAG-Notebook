"""ReviewService 测试 —— 间隔重复算法、到期查询与跨用户隔离。

间隔计算为纯函数；查询/标记走真实 SQLite 内存库验证过滤确实生效。
"""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chat_history import Base
from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.services.review_service import (
    INTERVALS,
    ReviewService,
    get_next_interval,
)

USER_A = "u-aaaa"
USER_B = "u-bbbb"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as s:
        # 种子：A 用户一条到期、一条未到期；B 用户一条到期
        now = datetime.now()
        n1 = Note(id="n-1", user_id=USER_A, title="到期笔记",
                  content="内容" * 50, category="tech")
        n2 = Note(id="n-2", user_id=USER_A, title="未到期",
                  content="稍后", category="life")
        n3 = Note(id="n-3", user_id=USER_B, title="他人笔记",
                  content="秘密内容", category="other")
        s.add_all([n1, n2, n3])
        s.add_all([
            ReviewRecord(id="r-1", user_id=USER_A, note_id="n-1",
                         review_count=0, interval_days=1,
                         next_review_at=now - timedelta(hours=1),
                         last_reviewed_at=None),
            ReviewRecord(id="r-2", user_id=USER_A, note_id="n-2",
                         review_count=2, interval_days=2,
                         next_review_at=now + timedelta(days=1),
                         last_reviewed_at=now - timedelta(days=2)),
            ReviewRecord(id="r-3", user_id=USER_B, note_id="n-3",
                         review_count=5, interval_days=15,
                         next_review_at=now - timedelta(hours=2),
                         last_reviewed_at=now - timedelta(days=15)),
        ])
        await s.commit()
        yield s
    await engine.dispose()


# ---------- 间隔算法 ----------

def test_next_interval_progression_and_cap():
    assert [get_next_interval(i) for i in range(6)] == INTERVALS
    assert get_next_interval(10) == 30      # 超出数组固定 30 天
    assert get_next_interval(6) == 30


# ---------- 到期查询 ----------

@pytest.mark.asyncio
async def test_get_today_reviews_only_own_due(db):
    svc = ReviewService()
    reviews = await svc.get_today_reviews(db, USER_A)
    assert [r["note_id"] for r in reviews] == ["n-1"]
    assert reviews[0]["title"] == "到期笔记"
    assert reviews[0]["content_preview"].startswith("内容")
    # B 用户的到期记录对 A 不可见（仅 A 的到期笔记出现）
    assert [r["title"] for r in reviews] == ["到期笔记"]


@pytest.mark.asyncio
async def test_count_due_reviews_scoped(db):
    svc = ReviewService()
    assert await svc.count_due_reviews(db, USER_A) == 1
    assert await svc.count_due_reviews(db, USER_B) == 1
    assert await svc.count_due_reviews(db, "nobody") == 0


# ---------- 标记回顾 ----------

@pytest.mark.asyncio
async def test_mark_reviewed_advances_interval(db):
    svc = ReviewService()
    out = await svc.mark_reviewed(db, "n-1", USER_A)
    assert out["success"] is True
    assert out["review_count"] == 1
    assert out["interval_days"] == 2        # INTERVALS[1] = 2 天

    out2 = await svc.mark_reviewed(db, "n-1", USER_A)
    assert out2["review_count"] == 2 and out2["interval_days"] == 4

    # 持久化后到期时间已推后
    fresh = await db.get(ReviewRecord, "r-1")
    assert fresh.review_count == 2
    assert fresh.interval_days == 4
    assert fresh.next_review_at > datetime.now()


@pytest.mark.asyncio
async def test_mark_reviewed_cross_user_rejected(db):
    svc = ReviewService()
    # A 尝试标记 B 的记录：查询按 (note_id, user_id) 过滤 → 不存在
    out = await svc.mark_reviewed(db, "n-3", USER_A)
    assert out == {"success": False, "message": "回顾记录不存在"}
    # B 的记录未被 A 污染
    fresh = await db.get(ReviewRecord, "r-3")
    assert fresh.review_count == 5


# ---------- 问题生成的降级兜底 ----------

@pytest.mark.asyncio
async def test_generate_question_failure_returns_fallback():
    # 单测环境无真实 LLM/缓存，generate_review_question 内部异常被捕获，
    # 必须回退到固定兜底文案而不是抛错
    svc = ReviewService()
    out = await svc.generate_review_question("笔记内容", llm_config={})
    assert out == "请回顾这篇笔记的主要内容"
