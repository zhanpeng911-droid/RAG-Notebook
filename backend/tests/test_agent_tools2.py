"""app/agent/agent_tools.py 测试 —— ContextVar 生命周期与笔记工具行为。

恢复真实 langchain_core（@tool 装饰器真实），note/review 服务与
数据库会话注入假实现，验证工具调用、无身份拒绝与异常回退。
"""
import importlib
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from tests.helpers.unmock import RETRIEVAL_STACK, restore_real

restore_real(*RETRIEVAL_STACK)

# 若 agent_tools 已被 test_agent.py 以 mock @tool 缓存进 sys.modules，
# restore 后 import 仍拿到旧缓存——无条件重载保证 @tool 为真实装饰器
import app.agent.agent_tools as at  # noqa: E402
at = importlib.reload(at)  # noqa: E402


async def _call(name, **kwargs):
    """@tool 装饰后是 StructuredTool，经 ainvoke 走真实参数校验调用。"""
    return await getattr(at, name).ainvoke(kwargs)


# ---------- ContextVar 生命周期 ----------

def test_context_var_set_get_reset():
    at.set_current_user_id("u-1")
    assert at.get_current_user_id_from_context() == "u-1"
    at.reset_current_user_id()
    assert at.get_current_user_id_from_context() is None

    def cb(d):
        return None
    at.set_thinking_callback(cb)
    assert at.get_thinking_callback_from_context() is cb
    at.set_thinking_callback(None)
    assert at.get_thinking_callback_from_context() is None

    at.set_llm_config({"model": "x"})
    assert at.get_llm_config_from_context() == {"model": "x"}
    at.reset_llm_config()
    assert at.get_llm_config_from_context() is None


# ---------- 无身份拒绝 ----------

@pytest.mark.asyncio
async def test_tools_require_user_identity():
    at.reset_current_user_id()
    args = {
        "search_notes_tool": {"query": "q"},
        "get_note_stats_tool": {},
        "get_today_reviews_tool": {},
        "mark_reviewed_tool": {"note_id": "note-1"},
        "create_note_tool": {"title": "标题", "content": ""},
        "get_related_notes_tool": {"note_id": "note-1"},
    }
    for name, kw in args.items():
        out = await _call(name, **kw)
        assert "无法确定用户身份" in out


# ---------- get_user_info_tools / what_time_is_now ----------

@pytest.mark.asyncio
async def test_get_user_info_tools_parses_jwt():
    from jose import jwt as jose_jwt
    from app.config.validator import get_settings
    token = jose_jwt.encode({"user_id": "u-abc", "user_name": "小明"},
                            get_settings().SECRET_KEY, algorithm="HS256")
    out = await _call("get_user_info_tools", token=token)
    assert "u-abc" in out and "小明" in out


@pytest.mark.asyncio
async def test_get_user_info_tools_invalid_token():
    out = await _call("get_user_info_tools", token="bad-token")
    assert "无法解析JWT" in out


def test_what_time_is_now_format():
    import asyncio
    result = asyncio.run(_call("what_time_is_now"))
    assert result.startswith("当前时间是：")


# ---------- 笔记/回顾工具（假服务 + 内存库） ----------

@pytest_asyncio.fixture
async def tool_env(monkeypatch):
    import app.db.db_config as db_cfg
    from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                        create_async_engine)
    from app.models.chat_history import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)
    monkeypatch.setattr(db_cfg, "AsyncSessionLocal", factory)

    fake_note = NS()
    async def no_notes(db, user_id, query, top_k=5):
        return []
    fake_note.search_notes = no_notes
    async def stats(db, user_id):
        return {"total": 0, "categories": [], "uncategorized": 0}
    fake_note.get_category_stats = stats
    async def related(db, note_id, user_id, top_k=3):
        return []
    fake_note.get_related_notes = related
    fake_note.create_note = None
    monkeypatch.setattr(at, "note_service", fake_note)

    fake_review = NS()
    async def today(db, user_id):
        return []
    fake_review.get_today_reviews = today
    async def mark(db, note_id, user_id):
        return {"success": False, "message": "回顾记录不存在"}
    fake_review.mark_reviewed = mark
    monkeypatch.setattr(at, "review_service", fake_review)

    at.set_current_user_id("u-ctx")
    yield NS(factory=factory, notes=fake_note, reviews=fake_review)
    at.reset_current_user_id()
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_notes_empty_result(tool_env):
    out = await _call("search_notes_tool", query="关键词")
    assert out == "未找到相关笔记"


@pytest.mark.asyncio
async def test_get_note_stats_empty(tool_env):
    out = await _call("get_note_stats_tool")
    assert "总笔记数: 0" in out


@pytest.mark.asyncio
async def test_get_today_reviews_empty(tool_env):
    out = await _call("get_today_reviews_tool")
    assert "今日没有待回顾的笔记" in out


@pytest.mark.asyncio
async def test_mark_reviewed_missing(tool_env):
    out = await _call("mark_reviewed_tool", note_id="nope")
    assert "回顾记录不存在" in out


@pytest.mark.asyncio
async def test_get_related_notes_empty(tool_env):
    out = await _call("get_related_notes_tool", note_id="n1")
    assert "未找到关联笔记" in out


@pytest.mark.asyncio
async def test_get_related_notes_formats_dict_shape(tool_env):
    # 回归：get_related_notes 返回 {"notes": [...], "knowledge_docs": [...]},
    # 工具此前按扁平列表迭代导致必现 TypeError（永远返回"获取关联推荐时出错"）
    async def related(db, note_id, user_id, top_k=3):
        return {
            "notes": [{"id": "n2", "title": "相关笔记",
                       "content_preview": "预览内容", "similarity": 0.8}],
            "knowledge_docs": [{"id": "kb1", "title": "知识库文档",
                                "content": "文档内容", "similarity": 0.6}],
        }
    tool_env.notes.get_related_notes = related
    out = await _call("get_related_notes_tool", note_id="n1")
    assert "获取关联推荐时出错" not in out
    assert "相关笔记" in out and "📝 笔记" in out
    assert "知识库文档" in out and "📚 知识库" in out


@pytest.mark.asyncio
async def test_search_notes_formats_result(tool_env):
    async def rich(db, user_id, query, top_k=5):
        return [NS(title="RAG 入门", category="study", tags=["rag", "笔记"],
                   content="这是一段足够长的内容" * 30)]
    tool_env.notes.search_notes = rich
    out = await _call("search_notes_tool", query="rag")
    assert "RAG 入门" in out and "study" in out


@pytest.mark.asyncio
async def test_tool_exception_returns_error_text(tool_env):
    async def boom(*a, **k):
        raise RuntimeError("磁盘故障")
    tool_env.notes.search_notes = boom
    out = await _call("search_notes_tool", query="x")
    assert "搜索笔记时出错" in out and "磁盘故障" in out
