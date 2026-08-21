"""
P0 修复回归测试。

覆盖：
- Guardrails 并发竞态：AgentGraph 实例级防护栏，超时计时互不干扰
- get_current_user_info 补齐 JWT 黑名单（撤销）检查
- 限流：INCR 原子计数语义 + 开关文件读取缓存
- Planner：SIMPLE 分类激活、CRAG 二轮 top_k 扩大且 space 范围不扩散
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


# ==================== Guardrails 并发竞态 ====================


def test_agent_graph_guardrails_are_instance_level():
    """每个 AgentGraph 必须持有独立的 Guardrails，禁止共享可变计时状态"""
    from app.agentic.graph import AgentGraph

    g1 = AgentGraph(user_id="user-1")
    g2 = AgentGraph(user_id="user-2")

    assert g1.guardrails is not g2.guardrails


def test_timeout_clock_not_reset_by_concurrent_request():
    """并发场景：请求 B 开始计时不得重置请求 A 的超时判断（原全局单例 bug 的回归守护）"""
    from app.agentic.graph import AgentGraph

    g1 = AgentGraph(user_id="user-1")
    g2 = AgentGraph(user_id="user-2")

    g1.guardrails.MAX_TOTAL_TIME = 0.05
    g1.guardrails.start()
    time.sleep(0.08)  # A 已超过 0.05s 预算

    g2.guardrails.start()  # B 开始计时，不应影响 A

    assert g1.guardrails.check_timeout() is False, "A 已超预算，超时防护必须生效"
    assert g2.guardrails.check_timeout() is True, "B 刚开始，不应被判超时"


# ==================== Planner 分类与检索计划 ====================


def test_classify_query_short_without_keywords_is_simple():
    """短查询且不含关键词 -> SIMPLE（Adaptive-RAG 轻量路径）"""
    from app.agentic.planner import planner, QueryType

    assert planner.classify_query("Redis持久化") is QueryType.SIMPLE


def test_classify_query_keyword_queries_not_simple():
    """含明确关键词的查询按关键词分类，不会被 SHORT 规则吞掉"""
    from app.agentic.planner import planner, QueryType

    assert planner.classify_query("什么是MVCC") is QueryType.FACTUAL
    assert planner.classify_query("为什么需要消息队列") is QueryType.EXPLANATORY
    assert planner.classify_query("RDB和AOF的区别") is QueryType.COMPARATIVE
    assert planner.classify_query("怎么部署") is QueryType.PROCEDURAL


def test_classify_query_long_without_keywords_is_exploratory():
    """长查询且不含关键词 -> EXPLORATORY"""
    from app.agentic.planner import planner, QueryType

    long_query = "介绍一下Redis主从复制过程中复制积压缓冲区的作用"
    assert planner.classify_query(long_query) is QueryType.EXPLORATORY


def test_simple_and_factual_share_lightweight_params():
    """SIMPLE 与 FACTUAL 共用轻量参数（跳过 HyDE），激活 SIMPLE 不改变检索行为"""
    from app.agentic.planner import planner

    p_simple = planner.create_plan("Redis持久化", "user-1")
    p_factual = planner.create_plan("什么是Redis持久化", "user-1")

    assert (p_simple.top_k, p_simple.use_hyde, p_simple.use_rerank) == (
        p_factual.top_k,
        p_factual.use_hyde,
        p_factual.use_rerank,
    )
    assert p_simple.use_hyde is False


def test_round2_expands_top_k_but_keeps_space_scope():
    """CRAG 第二轮：top_k 扩大，但用户指定的 space 范围必须保持不变"""
    from app.agentic.planner import planner

    p_round1 = planner.create_plan("什么是MVCC", "user-1", space_id="sp-1")
    p_round2 = planner.create_plan("什么是MVCC", "user-1", space_id="sp-1", retrieval_round=1)

    assert p_round1.scope == "space:sp-1"
    assert p_round2.scope == "space:sp-1", "space 是用户显式约束，二轮不得扩散"
    assert p_round2.top_k > p_round1.top_k


def test_rewrite_query_strips_quotes_and_truncates():
    """查询改写：去引号 + 截断到 50 字符"""
    from app.agentic.planner import planner

    long_query = '"' + "很长的查询内容" * 20 + '"'
    rewritten = planner.rewrite_query(long_query)

    assert '"' not in rewritten
    assert len(rewritten) <= 50


# ==================== JWT 黑名单检查 ====================


def _fake_redis_client(exists_return: int) -> AsyncMock:
    client = AsyncMock()
    client.exists = AsyncMock(return_value=exists_return)
    return client


@pytest.mark.asyncio
async def test_get_current_user_info_rejects_blacklisted_token():
    """get_current_user_info 必须执行黑名单检查：已撤销 token 返回 401"""
    from app.utils import auth_utils

    payload = {"user_id": "user-1", "username": "alice", "jti": "jti-1"}

    with patch.object(auth_utils, "decode_django_jwt", return_value=payload), \
         patch.object(auth_utils, "JWT_BLACKLIST_CHECK_ENABLED", True), \
         patch.object(auth_utils, "JWT_BLACKLIST_REDIS_URL", "redis://fake"), \
         patch.object(auth_utils.redis, "from_url", return_value=_fake_redis_client(1)):
        credentials = SimpleNamespace(credentials="revoked-token")

        with pytest.raises(HTTPException) as exc_info:
            await auth_utils.get_current_user_info(credentials)

    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_info_allows_active_token():
    """黑名单中不存在的 token 正常返回用户信息"""
    from app.utils import auth_utils

    payload = {"user_id": "user-1", "username": "alice", "jti": "jti-1"}

    with patch.object(auth_utils, "decode_django_jwt", return_value=payload), \
         patch.object(auth_utils, "JWT_BLACKLIST_CHECK_ENABLED", True), \
         patch.object(auth_utils, "JWT_BLACKLIST_REDIS_URL", "redis://fake"), \
         patch.object(auth_utils.redis, "from_url", return_value=_fake_redis_client(0)):
        credentials = SimpleNamespace(credentials="active-token")

        result = await auth_utils.get_current_user_info(credentials)

    assert result == {"user_id": "user-1", "username": "alice"}


@pytest.mark.asyncio
async def test_get_current_user_id_still_rejects_blacklisted_token():
    """get_current_user_id 的黑名单行为不因重构回归"""
    from app.utils import auth_utils

    payload = {"user_id": "user-1", "username": "alice", "jti": "jti-1"}

    with patch.object(auth_utils, "decode_django_jwt", return_value=payload), \
         patch.object(auth_utils, "JWT_BLACKLIST_CHECK_ENABLED", True), \
         patch.object(auth_utils, "JWT_BLACKLIST_REDIS_URL", "redis://fake"), \
         patch.object(auth_utils.redis, "from_url", return_value=_fake_redis_client(1)):
        credentials = SimpleNamespace(credentials="revoked-token")

        with pytest.raises(HTTPException) as exc_info:
            await auth_utils.get_current_user_id(credentials)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_blacklist_check_unavailable_redis_returns_503():
    """无法确认撤销状态（Redis 异常）时返回 503，而不是放行"""
    from app.utils import auth_utils

    payload = {"user_id": "user-1", "username": "alice", "jti": "jti-1"}

    def _raise_from_url(*args, **kwargs):
        raise RuntimeError("redis down")

    with patch.object(auth_utils, "decode_django_jwt", return_value=payload), \
         patch.object(auth_utils, "JWT_BLACKLIST_CHECK_ENABLED", True), \
         patch.object(auth_utils, "JWT_BLACKLIST_REDIS_URL", "redis://fake"), \
         patch.object(auth_utils.redis, "from_url", side_effect=_raise_from_url):
        credentials = SimpleNamespace(credentials="some-token")

        with pytest.raises(HTTPException) as exc_info:
            await auth_utils.get_current_user_info(credentials)

    assert exc_info.value.status_code == 503


# ==================== 限流 ====================


class FakeRedis:
    """最小化 Redis 模拟：实现限流依赖用到的 incr/expire/ttl"""

    def __init__(self):
        self.data = {}
        self.ttls = {}

    async def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def expire(self, key, window):
        self.ttls[key] = window

    async def ttl(self, key):
        if key not in self.data:
            return -2
        return self.ttls.get(key, -1)


def _fake_request(path="/chat/agent/query"):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )


@pytest.mark.asyncio
async def test_rate_limit_rejects_request_over_limit(monkeypatch):
    """超过 limit 的请求必须被拒绝（429），窗口内计数语义与旧实现一致"""
    from app.core import rate_limit as rl

    fake = FakeRedis()
    monkeypatch.setattr(rl, "connect_redis", AsyncMock(return_value=fake))
    monkeypatch.setattr(rl, "is_redis_available", lambda: True)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")

    dependency = rl.rate_limit(limit=2, window=60)

    assert await dependency(_fake_request()) is None  # 第 1 次
    assert await dependency(_fake_request()) is None  # 第 2 次

    with pytest.raises(HTTPException) as exc_info:
        await dependency(_fake_request())  # 第 3 次超限

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_sets_window_ttl_on_first_request(monkeypatch):
    """首次请求必须设置窗口 TTL，避免 key 永不过期"""
    from app.core import rate_limit as rl

    fake = FakeRedis()
    monkeypatch.setattr(rl, "connect_redis", AsyncMock(return_value=fake))
    monkeypatch.setattr(rl, "is_redis_available", lambda: True)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")

    dependency = rl.rate_limit(limit=5, window=60)
    await dependency(_fake_request())

    assert list(fake.ttls.values()) == [60]


@pytest.mark.asyncio
async def test_rate_limit_self_heals_orphan_key_without_ttl(monkeypatch):
    """INCR 与 EXPIRE 之间中断产生的无 TTL key 必须被自动修复，避免用户被永久锁死"""
    from app.core import rate_limit as rl

    fake = FakeRedis()
    fake.data["rate_limit:chat:agent:query:ip:127.0.0.1"] = 3  # 已存在但丢失 TTL 的 key
    monkeypatch.setattr(rl, "connect_redis", AsyncMock(return_value=fake))
    monkeypatch.setattr(rl, "is_redis_available", lambda: True)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")

    dependency = rl.rate_limit(limit=10, window=60)
    await dependency(_fake_request())

    key = "rate_limit:chat:agent:query:ip:127.0.0.1"
    assert fake.ttls.get(key) == 60


@pytest.mark.asyncio
async def test_rate_limit_degrades_when_redis_unavailable(monkeypatch):
    """Redis 不可用时降级放行，不影响正常请求"""
    from app.core import rate_limit as rl

    monkeypatch.setattr(rl, "is_redis_available", lambda: False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")

    dependency = rl.rate_limit(limit=1, window=60)
    assert await dependency(_fake_request()) is None


def test_env_file_flag_read_is_cached(monkeypatch):
    """.env 文件的限流开关只读一次盘（进程内缓存），环境变量优先级保持动态"""
    from app.core import rate_limit as rl

    read_calls = []

    def _fake_read():
        read_calls.append(1)
        return True

    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.setattr(rl, "_read_rate_limit_flag_from_env_file", _fake_read)
    monkeypatch.setattr(rl, "_env_file_flag_cache", rl._UNSET)

    assert rl._is_rate_limit_enabled() is True
    assert rl._is_rate_limit_enabled() is True
    assert len(read_calls) == 1, ".env 文件只应被读取一次"

    # 环境变量优先级高于文件缓存，且保持动态
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    assert rl._is_rate_limit_enabled() is False
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    assert rl._is_rate_limit_enabled() is True
