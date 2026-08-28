"""功能验收 Part A-1：JWT 鉴权、笔记 CRUD 与用户隔离。

无外部依赖，TestClient + 内存 SQLite 全自动。
"""
import httpx
import pytest



@pytest.fixture
def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


@pytest.mark.asyncio
async def test_jwt_auth_required(client, auth_a):
    """无 token → 401；合法 token → 200。"""
    r = await client.get("/api/v1/note/list")
    assert r.status_code == 401
    r = await client.get("/api/v1/note/list", headers=auth_a)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_jwt_bad_token_rejected(client):
    r = await client.get("/api/v1/note/list",
                         headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_note_crud_and_isolation(client, auth_a, auth_b, factory):
    # A 创建笔记
    r = await client.post("/api/v1/note/create", headers=auth_a,
                          json={"title": "功能验收笔记", "content": "正文"})
    assert r.status_code == 200
    note_id = r.json()["data"]["id"]

    # A 能读到
    r = await client.get(f"/api/v1/note/{note_id}", headers=auth_a)
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "功能验收笔记"

    # B 越权读 → 404（隔离）
    r = await client.get(f"/api/v1/note/{note_id}", headers=auth_b)
    assert r.status_code == 404

    # A 更新
    r = await client.put(f"/api/v1/note/{note_id}", headers=auth_a,
                         json={"title": "改名"})
    assert r.status_code == 200

    # B 越权更新 → 404
    r = await client.put(f"/api/v1/note/{note_id}", headers=auth_b,
                         json={"title": "偷改"})
    assert r.status_code == 404

    # A 删除
    r = await client.delete(f"/api/v1/note/{note_id}", headers=auth_a)
    assert r.status_code == 200
    r = await client.get(f"/api/v1/note/{note_id}", headers=auth_a)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_note_list_pagination(client, auth_a, factory):
    for i in range(3):
        await client.post("/api/v1/note/create", headers=auth_a,
                          json={"title": f"n{i}", "content": "c"})
    r = await client.get("/api/v1/note/list?page=1&page_size=2", headers=auth_a)
    assert r.status_code == 200
    assert r.json()["data"]["total_count"] >= 3
