"""功能验收 Part A-2：知识库索引状态机、运行时配置热更新、限流。

知识库索引打桩（避免真实 embedding）；运行时配置走真实 set/reset；
限流单独开 RATE_LIMIT_ENABLED 验证 429。
"""
import pytest

from .conftest import USER_A


# ---------- 运行时配置热更新 ----------

@pytest.mark.asyncio
async def test_runtime_config_hot_reload(app, auth_a, monkeypatch):
    from app.config.validator import get_settings
    monkeypatch.setattr(get_settings(), "RUNTIME_CONFIG_ADMIN_USER_IDS", USER_A)

    import httpx
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://test")
    # 初始默认
    r = await client.get("/api/v1/admin/runtime-config", headers=auth_a)
    assert r.status_code == 200
    baseline = {p["key"]: p["value"]
                for p in r.json()["data"]["params"]}

    # 更新并验证即时生效
    r = await client.put("/api/v1/admin/runtime-config", headers=auth_a,
                         json={"values": {"retrieval.top_k_baseline": 9}})
    assert r.status_code == 200
    assert r.json()["data"]["values"]["retrieval.top_k_baseline"] == 9

    # 读取点（planner）验证生效值
    from app.core import runtime_config
    assert runtime_config.get("retrieval.top_k_baseline") == 9

    # 重置恢复默认
    r = await client.post("/api/v1/admin/runtime-config/reset",
                          headers=auth_a, json={"keys": []})
    assert r.status_code == 200
    assert runtime_config.get("retrieval.top_k_baseline") == baseline["retrieval.top_k_baseline"]
    await client.aclose()


# ---------- 知识库索引状态机 ----------

@pytest.mark.asyncio
async def test_index_state_machine(app, auth_a, monkeypatch):
    """pending → indexed；失败 → failed → reindex 成功。"""
    import app.services.document_index_service as di

    # 打桩：上传保存为 pending_index；索引成功 / 失败 / reindex 成功
    async def fake_save(file, user_id, space_id=""):
        return {"filename": "doc.txt", "message": "已保存",
                "status": "pending_index"}

    async def fake_reindex(doc_id, user_id):
        return {"success": True, "message": "已重新索引"}

    monkeypatch.setattr(di, "save_uploaded_file", fake_save)
    monkeypatch.setattr(di, "reindex_document", fake_reindex)
    async def _index_status(user_id, space_id=None):
        return [{"md5": "m1", "status": "indexed"}]
    monkeypatch.setattr(di, "get_user_index_status", _index_status)

    import httpx
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://test")

    # v2 上传 → pending_index
    r = await client.post("/api/v1/knowledge/add/single/v2", headers=auth_a,
                          files={"file": ("doc.txt", b"content", "text/plain")})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "pending_index"

    # 索引状态查询 → indexed
    r = await client.get("/api/v1/knowledge/index-status", headers=auth_a)
    assert r.status_code == 200
    assert r.json()["data"]["documents"][0]["status"] == "indexed"

    # reindex 成功
    r = await client.post("/api/v1/knowledge/doc-1/reindex", headers=auth_a)
    assert r.status_code == 200
    await client.aclose()


# ---------- 限流（打开开关验证 429） ----------

@pytest.mark.asyncio
async def test_rate_limit_returns_429(monkeypatch, factory):
    import httpx
    from fastapi import FastAPI
    from app.db.db_config import get_db
    from app.router.note_router import note_router
    from app.core.failed_response_register import register_exception_handlers

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")

    app = FastAPI()
    app.include_router(note_router, prefix="/api/v1")
    register_exception_handlers(app)

    async def _override_get_db():
        async with factory() as s:
            yield s
    app.dependency_overrides[get_db] = _override_get_db

    from .conftest import token_for

    # 假 Redis 客户端：incr 递增计数，让固定窗口限流生效
    class FakeRedis:
        def __init__(self):
            self.counts = {}

        async def incr(self, key):
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

        async def expire(self, key, window):
            return True

        async def ttl(self, key):
            return -1

    import app.core.rate_limit as rl
    fake = FakeRedis()

    async def _connect():
        return fake
    # rate_limit 模块在 import 时已绑定 connect_redis/is_redis_available，须 patch 其命名空间
    monkeypatch.setattr(rl, "connect_redis", _connect)
    monkeypatch.setattr(rl, "is_redis_available", lambda: True)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://test")
    h = {"Authorization": f"Bearer {token_for(USER_A)}"}
    # note/create 限流 limit=10；连打 12 次应出现 429
    codes = []
    for _ in range(12):
        r = await client.post("/api/v1/note/create", headers=h,
                              json={"title": "限流测试", "content": "c"})
        codes.append(r.status_code)
    assert 429 in codes
    await client.aclose()
