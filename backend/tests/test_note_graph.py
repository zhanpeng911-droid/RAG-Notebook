"""V2 P3/P4 笔记链接与图谱测试（不依赖 AI/embedding）。"""
import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from app.config.validator import get_settings
from app.models import Base
from app.models.note import Note
from app.models.note_graph import NoteLinkOccurrence, NoteLinkStatus
from app.services.note_link_service import (
    add_manual_relation, extract_links, remove_manual_relation,
    remove_note_links, sync_note_links,
)

import app.db.db_config as db_config

SECRET = get_settings().SECRET_KEY
OWNER = "u-note-owner-0000-0000-000000000001"
OTHER = "u-note-other-0000-0000-000000000002"

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"
D = "44444444-4444-4444-4444-444444444444"


def _token(user_id: str) -> str:
    return jose_jwt.encode({"user_id": user_id, "user_name": "u"},
                           SECRET, algorithm="HS256")


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_config, "AsyncSessionLocal", session_factory)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    yield session_factory
    await engine.dispose()


async def _add_note(factory, note_id: str, title: str, content: str,
                    user_id: str = OWNER, tags=None, category=None):
    async with factory() as session:
        session.add(Note(id=note_id, user_id=user_id, title=title,
                         content=content, tags=tags, category=category))
        await session.commit()
        note = await session.get(Note, note_id)
        await sync_note_links(session, note)
        await session.commit()


@pytest_asyncio.fixture
async def seeded(factory):
    # 目标笔记先存在，标题链接才能在保存时唯一解析
    await _add_note(factory, C, "评测", "评测笔记。")
    await _add_note(factory, B, "向量检索", "向量检索依赖 [[评测]]。")
    await _add_note(factory, A, "RAG", "RAG 引用 [[note:%s|向量检索]]，再一次 [[向量检索]]。" % B)
    await _add_note(factory, D, "孤立笔记", "没有链接。")
    return factory


@pytest_asyncio.fixture
async def client(factory):
    from fastapi import FastAPI
    from app.core.failed_response_register import register_exception_handlers
    from app.db.db_config import get_db
    from app.router.note_graph_router import note_graph_router

    app = FastAPI()
    app.include_router(note_graph_router, prefix="/api/v1")
    register_exception_handlers(app)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------

def test_extract_links_forms_and_offsets():
    text = "前文 [[note:%s|别名]] 后文，再见 [[标题]]。" % B
    links = extract_links(text)
    assert len(links) == 2
    assert links[0].target_kind == "id"
    assert links[0].target_value == B
    assert links[0].display_text == "别名"
    assert text[links[0].char_start:links[0].char_end] == links[0].raw_token
    assert links[1].target_kind == "title"
    assert links[1].target_value == "标题"


def test_extract_links_ignores_code_and_escaped():
    text = (
        "正文 [[有效]]\n\n"
        "```\n[[代码围栏]]\n```\n\n"
        "行内 `[[行内代码]]` 结束\n\n"
        "转义 \\[[不应解析]]\n"
    )
    links = extract_links(text)
    assert [link.target_value for link in links] == ["有效"]


@pytest.mark.asyncio
async def test_sync_occurrences_is_idempotent_and_updates(factory):
    await _add_note(factory, B, "B", "B 正文")
    await _add_note(factory, A, "A", "两次 [[B]] 和 [[B]]")
    async with factory() as session:
        rows = list((await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == A)
        )).scalars().all())
        assert len(rows) == 2
        assert {row.target_note_id for row in rows} == {B}
        assert {row.status for row in rows} == {NoteLinkStatus.RESOLVED}

        note = await session.get(Note, A)
        note.content = "只剩一次 [[B]]"
        await session.commit()
        await sync_note_links(session, note)
        await session.commit()
        rows = list((await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == A)
        )).scalars().all())
        assert len(rows) == 1

        note.content = "链接已删除"
        await session.commit()
        await sync_note_links(session, note)
        await session.commit()
        rows = list((await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == A)
        )).scalars().all())
        assert rows == []


@pytest.mark.asyncio
async def test_duplicate_title_is_ambiguous(factory):
    await _add_note(factory, B, "同名", "B")
    await _add_note(factory, C, "同名", "C")
    await _add_note(factory, A, "A", "[[同名]]")
    async with factory() as session:
        row = (await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == A)
        )).scalar_one()
        assert row.target_note_id is None
        assert row.status == NoteLinkStatus.AMBIGUOUS


@pytest.mark.asyncio
async def test_target_delete_keeps_source_occurrence_unresolved(factory):
    await _add_note(factory, B, "B", "B")
    await _add_note(factory, A, "A", "[[note:%s|B]]" % B)
    async with factory() as session:
        await remove_note_links(session, B, OWNER)
        await session.commit()
        row = (await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == A)
        )).scalar_one()
        assert row.target_note_id is None
        assert row.status == NoteLinkStatus.TARGET_DELETED


# ---------------------------------------------------------------------------
# 手动关联
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manual_relation_add_remove_and_permissions(factory):
    await _add_note(factory, A, "A", "")
    await _add_note(factory, B, "B", "")
    async with factory() as session:
        relation = await add_manual_relation(
            session, owner_user_id=OWNER, source_note_id=A,
            target_note_id=B, reason="主题相关")
        await session.commit()
        again = await add_manual_relation(
            session, owner_user_id=OWNER, source_note_id=A,
            target_note_id=B)
        assert again.id == relation.id
        assert await remove_manual_relation(
            session, owner_user_id=OWNER, note_id=A,
            relation_id=relation.id) is True
        await session.commit()
        with pytest.raises(LookupError):
            await add_manual_relation(
                session, owner_user_id=OTHER, source_note_id=A,
                target_note_id=B)


# ---------------------------------------------------------------------------
# 图 API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_global_graph_and_filters(client, seeded):
    async with seeded() as session:
        await add_manual_relation(
            session, owner_user_id=OWNER, source_note_id=A,
            target_note_id=C, reason="手动")
        await session.commit()

    response = await client.get("/api/v1/notes/graph", headers=_auth(OWNER))
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["nodes"]) == 4
    assert len(data["edges"]) == 3
    edge_types = {edge["type"] for edge in data["edges"]}
    assert edge_types == {"explicit_link", "manual_relation"}
    explicit = next(e for e in data["edges"] if e["type"] == "explicit_link"
                    and e["source"] == A and e["target"] == B)
    assert explicit["count"] == 2

    no_orphans = await client.get(
        "/api/v1/notes/graph", params={"include_orphans": "false"},
        headers=_auth(OWNER))
    assert len(no_orphans.json()["data"]["nodes"]) == 3

    explicit_only = await client.get(
        "/api/v1/notes/graph", params={"edge_type": "explicit"},
        headers=_auth(OWNER))
    assert len(explicit_only.json()["data"]["edges"]) == 2

    other = await client.get("/api/v1/notes/graph", headers=_auth(OTHER))
    assert other.json()["data"]["nodes"] == []
    assert other.json()["data"]["edges"] == []


@pytest.mark.asyncio
async def test_local_graph_depth_and_links(client, seeded):
    one = await client.get(f"/api/v1/notes/{A}/local-graph",
                           params={"depth": 1}, headers=_auth(OWNER))
    assert one.status_code == 200
    one_data = one.json()["data"]
    assert {node["id"] for node in one_data["nodes"]} == {A, B}

    two = await client.get(f"/api/v1/notes/{A}/local-graph",
                           params={"depth": 2}, headers=_auth(OWNER))
    assert {node["id"] for node in two.json()["data"]["nodes"]} == {A, B, C}

    links = await client.get(f"/api/v1/notes/{A}/links", headers=_auth(OWNER))
    data = links.json()["data"]
    assert data["outgoing"][0]["note"]["id"] == B
    assert data["outgoing"][0]["count"] == 2
    assert data["incoming"] == []

    backlinks = await client.get(f"/api/v1/notes/{B}/links", headers=_auth(OWNER))
    assert backlinks.json()["data"]["incoming"][0]["note"]["id"] == A

    missing = await client.get(f"/api/v1/notes/{A}/links", headers=_auth(OTHER))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_link_candidates_and_manual_api(client, seeded):
    candidates = await client.get(
        "/api/v1/notes/link-candidates", params={"q": "向量"},
        headers=_auth(OWNER))
    assert candidates.status_code == 200
    assert [c["id"] for c in candidates.json()["data"]["candidates"]] == [B]

    created = await client.post(
        f"/api/v1/notes/{A}/manual-relations",
        json={"target_note_id": D, "reason": "手动关联"},
        headers=_auth(OWNER))
    assert created.status_code == 200
    relation_id = created.json()["data"]["relation_id"]

    graph = await client.get("/api/v1/notes/graph", headers=_auth(OWNER))
    manual_edges = [e for e in graph.json()["data"]["edges"]
                    if e["type"] == "manual_relation"]
    assert manual_edges and manual_edges[0]["source"] == A
    assert manual_edges[0]["target"] == D

    removed = await client.delete(
        f"/api/v1/notes/{A}/manual-relations/{relation_id}",
        headers=_auth(OWNER))
    assert removed.status_code == 200

    forbidden = await client.post(
        f"/api/v1/notes/{A}/manual-relations",
        json={"target_note_id": D}, headers=_auth(OTHER))
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_note_service_save_rebuilds_links(factory):
    from app.schemas.models import NoteCreate, NoteUpdate
    from app.services.note_service import note_service

    async with factory() as session:
        target = await note_service.create_note(
            session, OWNER, NoteCreate(title="目标", content="内容"))
        created = await note_service.create_note(
            session, OWNER, NoteCreate(title="来源", content="[[目标]]"))
        await session.commit()

        rows = list((await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == created.id)
        )).scalars().all())
        assert len(rows) == 1
        assert rows[0].target_note_id == target.id

        await note_service.update_note(
            session, created.id, OWNER,
            NoteUpdate(content="不再链接"))
        await session.commit()
        rows = list((await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == created.id)
        )).scalars().all())
        assert rows == []

        await note_service.delete_note(session, target.id, OWNER)
        await session.commit()


@pytest.mark.asyncio
async def test_rename_target_keeps_stable_id_link(factory):
    await _add_note(factory, B, "原名称", "B")
    await _add_note(factory, A, "A", "[[note:%s|原名称]]" % B)
    async with factory() as session:
        target = await session.get(Note, B)
        target.title = "改名之后"
        await session.commit()
        row = (await session.execute(
            select(NoteLinkOccurrence).where(
                NoteLinkOccurrence.source_note_id == A)
        )).scalar_one()
        assert row.target_note_id == B
        assert row.status == NoteLinkStatus.RESOLVED


@pytest.mark.asyncio
async def test_manual_removal_keeps_explicit_links(client, seeded):
    async with seeded() as session:
        relation = await add_manual_relation(
            session, owner_user_id=OWNER, source_note_id=A,
            target_note_id=D)
        await session.commit()
        relation_id = relation.id

    before = await client.get("/api/v1/notes/graph", headers=_auth(OWNER))
    assert len(before.json()["data"]["edges"]) == 3

    removed = await client.delete(
        f"/api/v1/notes/{A}/manual-relations/{relation_id}",
        headers=_auth(OWNER))
    assert removed.status_code == 200

    after = await client.get("/api/v1/notes/graph", headers=_auth(OWNER))
    edges = after.json()["data"]["edges"]
    assert len(edges) == 2
    assert all(edge["type"] == "explicit_link" for edge in edges)


# ---------------------------------------------------------------------------
# 直接调用服务/路由，覆盖异步续行与分支（不依赖 HTTP 传输层）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_note_graph_service_branches_directly(seeded):
    from app.services.note_graph_service import (
        get_local_graph, get_note_graph, get_note_links, link_candidates,
    )

    async with seeded() as session:
        graph = await get_note_graph(
            session, owner_user_id=OWNER, edge_type="all",
            include_orphans=True, limit=10)
        assert len(graph["nodes"]) == 4
        assert len(graph["edges"]) == 2

        no_orphans = await get_note_graph(
            session, owner_user_id=OWNER, include_orphans=False, limit=10)
        assert len(no_orphans["nodes"]) == 3

        manual_only = await get_note_graph(
            session, owner_user_id=OWNER, edge_type="manual", limit=10)
        assert manual_only["edges"] == []

        limited = await get_note_graph(
            session, owner_user_id=OWNER, limit=1)
        assert limited["counts"]["truncated"] is True

        one = await get_local_graph(
            session, owner_user_id=OWNER, note_id=A, depth=1)
        assert {node["id"] for node in one["nodes"]} == {A, B}
        two = await get_local_graph(
            session, owner_user_id=OWNER, note_id=A, depth=2)
        assert {node["id"] for node in two["nodes"]} == {A, B, C}
        assert await get_local_graph(
            session, owner_user_id=OWNER, note_id="missing", depth=1) is None

        links = await get_note_links(session, owner_user_id=OWNER, note_id=A)
        assert links["outgoing"][0]["count"] == 2
        assert links["counts"]["outgoing_notes"] == 1
        assert await get_note_links(
            session, owner_user_id=OWNER, note_id="missing") is None

        candidates = await link_candidates(
            session, owner_user_id=OWNER, q="向量", limit=5,
            exclude_note_id=A)
        assert [item["id"] for item in candidates] == [B]

        empty = await get_note_graph(
            session, owner_user_id=OTHER, limit=10)
        assert empty["nodes"] == []
        assert empty["edges"] == []


@pytest.mark.asyncio
async def test_note_graph_service_tag_category_and_duplicates(seeded):
    from app.services.note_graph_service import get_note_graph, link_candidates

    async with seeded() as session:
        note = await session.get(Note, A)
        note.tags = ["ai"]
        note.category = "study"
        duplicate = Note(id="dup-1", user_id=OWNER, title="同名", content="")
        duplicate2 = Note(id="dup-2", user_id=OWNER, title="同名", content="")
        session.add_all([duplicate, duplicate2])
        await session.commit()

        tagged = await get_note_graph(
            session, owner_user_id=OWNER, tag="ai", limit=10)
        assert [node["id"] for node in tagged["nodes"]] == [A]

        categorized = await get_note_graph(
            session, owner_user_id=OWNER, category="study", limit=10)
        assert [node["id"] for node in categorized["nodes"]] == [A]

        candidates = await link_candidates(
            session, owner_user_id=OWNER, q="同名", limit=5)
        assert len(candidates) == 2
        assert all(item["duplicate_title"] for item in candidates)


@pytest.mark.asyncio
async def test_note_graph_router_direct_calls(seeded):
    from fastapi import HTTPException
    from app.router.note_graph_router import (
        ManualRelationCreate, create_manual_relation, delete_manual_relation,
        get_link_candidates, note_graph, note_links, note_local_graph,
    )

    async with seeded() as session:
        graph = await note_graph(
            q=None, tag=None, category=None, edge_type="all",
            include_orphans=True, limit=10, user_id=OWNER, db=session)
        assert graph.status_code == 200

        candidates = await get_link_candidates(
            q="向量", limit=10, exclude_note_id=A, user_id=OWNER, db=session)
        assert candidates.status_code == 200

        local = await note_local_graph(
            A, depth=1, limit=200, user_id=OWNER, db=session)
        assert local.status_code == 200

        links = await note_links(A, user_id=OWNER, db=session)
        assert links.status_code == 200

        import json as _json
        created = await create_manual_relation(
            A, ManualRelationCreate(target_note_id=D),
            user_id=OWNER, db=session)
        assert created.status_code == 200
        relation_id = _json.loads(created.body)["data"]["relation_id"]
        removed = await delete_manual_relation(
            A, relation_id, user_id=OWNER, db=session)
        assert removed.status_code == 200

        with pytest.raises(HTTPException):
            await note_local_graph(
                "missing", depth=1, limit=200, user_id=OWNER, db=session)
        with pytest.raises(HTTPException):
            await note_links("missing", user_id=OWNER, db=session)
