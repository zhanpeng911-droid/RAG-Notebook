"""Explicit, loopback-only browser acceptance server; NEVER a deployment entry.

Uses real production routers/services and an ephemeral SQLite database. Only
login, model output, unrelated dashboard endpoints and background dispatch are
test substitutes. Run from repository root with backend/.venv/Scripts/python.exe.
No business database, vector store, Redis or external provider can be reached.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update({
    "ENV": "test", "SECRET_KEY": "isolated-browser-fixture-not-a-production-secret",
    "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "1", "MYSQL_USER": "isolated",
    "MYSQL_PASSWORD": "isolated", "MYSQL_DATABASE": "isolated",
    "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "1", "REDIS_PASSWORD": "",
    "REDIS_CACHE_URL": "", "JWT_BLACKLIST_REDIS_URL": "",
    "CELERY_BROKER_URL": "redis://127.0.0.1:1/15",
    "CELERY_RESULT_BACKEND": "redis://127.0.0.1:1/15",
    "OPENAI_API_KEY": "not-a-real-key", "OPENAI_API_BASE": "http://127.0.0.1:1/v1",
    "LLM_TYPE": "OLLAMA", "VISION_MODEL_TYPE": "OPENAI",
    "NOTE_VECTOR_INDEX_ENABLED": "false", "NOTE_AUTO_TAG_ENABLED": "false",
    "RATE_LIMIT_ENABLED": "false",
})

import asyncio  # noqa: E402
import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import tests.conftest  # noqa: E402,F401 -- prevent heavy/remote initialization
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from jose import jwt  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.db.db_config as db_config  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.note import Note  # noqa: E402
from app.models.note_graph import NoteManualRelation  # noqa: E402
from app.models.knowledge_insight import KnowledgeGenerationJob  # noqa: E402
from app.repositories.knowledge_insight_repository import KnowledgeInsightRepository  # noqa: E402
from app.router.insight_router import insight_router  # noqa: E402
from app.router.note_graph_router import note_graph_router  # noqa: E402
from app.router.note_router import note_router  # noqa: E402
from app.core.failed_response_register import register_exception_handlers  # noqa: E402
from app.core.success_response import success_response  # noqa: E402
from app.services.note_link_service import sync_note_links, add_manual_relation  # noqa: E402
from app.services.insight_generation_service import claim_and_run_job  # noqa: E402
from tests.test_knowledge_insights import _seed_document, _seed_version, _llm_payload, _FakeChatModel  # noqa: E402

OWNER = "browser-fixture-owner"
SCALE = "browser-fixture-scale"
engine = create_async_engine("sqlite+aiosqlite:///:memory:")
factory = async_sessionmaker(engine, expire_on_commit=False)
db_config.AsyncSessionLocal = factory
pending = set()


async def generate(job_id):
    await asyncio.sleep(1)  # deterministic queue boundary, not UI synchronization
    async with factory() as db:
        job = await db.get(KnowledgeGenerationJob, job_id)
        anchors = await KnowledgeInsightRepository(db).list_anchors(job.source_version_id)
        ids = [anchor.id for anchor in anchors]
        payload = _llm_payload(ids if len(ids) > 1 else ids * 2)
        if job.owner_user_id == SCALE:
            payload["sections"] = [{"title": f"章节 {i}", "anchor_ids": ids[:1],
                "summary": f"章节 {i} 总结", "claims": [
                    {"text": f"主题 {i} · 结论 {j}", "anchor_ids": ids[:1]}
                    for j in range(8)]} for i in range(12)]
    await claim_and_run_job(job_id, chat_model=_FakeChatModel(payload))


def dispatch(job_id):
    task = asyncio.create_task(generate(job_id))
    pending.add(task)
    task.add_done_callback(pending.discard)


import app.router.insight_router as router_module  # noqa: E402
router_module.dispatch_insight_job = dispatch


@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    titles = ["验收 A · 知识工作台", "验收 B · 来源追溯", "验收 C · 稳定身份",
              "同名笔记", "同名笔记", "孤立笔记", "手动关联", "双向链接",
              "未解析示例", "重复引用", "代码示例", "权限边界"]
    async with factory() as db:
        for i, title in enumerate(titles):
            text = f"# {title}\n\n第一节：这是一份公开的验收样本。\n\n## 原文证据\n\n第二节：引用必须关联到正确的资源身份。"
            if i == 0:
                text += "\n\n参见 [[验收 B · 来源追溯]] 与 [[验收 C · 稳定身份]]。"
            elif i == 1:
                text += "\n\n参见 [[验收 C · 稳定身份]]。"
            elif i == 8:
                text += "\n\n[[不存在的笔记]]"
            elif i == 9:
                text += "\n\n[[验收 B · 来源追溯]]，再次 [[验收 B · 来源追溯]]。"
            elif i == 10:
                text += "\n\n`[[代码不应成为链接]]`"
            db.add(Note(id=f"00000000-0000-0000-0000-{i + 1:012d}", user_id=OWNER,
                        title=title, content=text, category="project", tags=["验收"] ))
        db.add(Note(id="other-owner-note", user_id="other", title="不应泄漏的另一账号", content="private"))
        await db.commit()
        for i in range(12):
            await sync_note_links(db, await db.get(Note, f"00000000-0000-0000-0000-{i + 1:012d}"))
        await add_manual_relation(db, owner_user_id=OWNER,
            source_note_id="00000000-0000-0000-0000-000000000001",
            target_note_id="00000000-0000-0000-0000-000000000007", reason="验收手动关联")
        await db.commit()
        for i in range(1000):
            db.add(Note(id=f"perf-{i:04d}", user_id=SCALE, title=f"性能样本 {i:04d}",
                        content="不应在全局图接口返回的正文", category="project", tags=["规模"]))
        for i in range(1000):
            source = 700 + i % 300
            target = 700 + (i % 300 + i // 300 + 1) % 300
            db.add(NoteManualRelation(id=str(uuid.uuid4()), owner_user_id=SCALE,
                source_note_id=f"perf-{source:04d}", target_note_id=f"perf-{target:04d}",
                relation_type="manual", created_by=SCALE))
        await db.commit()
    await _seed_document(factory, user_id=OWNER)
    await _seed_version(factory, user_id=OWNER)
    await _seed_document(factory, user_id=SCALE, doc_id="scale-document")
    await _seed_version(factory, user_id=SCALE, doc_id="scale-document")
    yield
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)
app.include_router(note_graph_router, prefix="/api/v1")
app.include_router(note_router, prefix="/api/v1")
app.include_router(insight_router, prefix="/api/v1")


async def isolated_db():
    async with factory() as db:
        yield db


app.dependency_overrides[db_config.get_db] = isolated_db


@app.post("/user/login/")
async def login(request: Request):
    data = await request.json()
    if data.get("username") not in ("fixture", "scale") or data.get("password") != "fixture-only":
        raise HTTPException(401, "仅接受隔离验收账号")
    user_id = OWNER if data["username"] == "fixture" else SCALE
    token = jwt.encode({"user_id": user_id, "user_name": "fixture",
        "exp": datetime.now(timezone.utc) + timedelta(hours=2)}, os.environ["SECRET_KEY"], algorithm="HS256")
    return {"code": 200, "token": token, "user": {"id": user_id, "username": "隔离验收"}}


@app.get("/user/detail/")
async def profile():
    return {"id": OWNER, "username": "隔离验收"}


@app.get("/api/v1/chat/sessions")
async def sessions():
    return success_response(data={"sessions": []})


@app.get("/api/v1/review/due-count")
async def due_count():
    return success_response(data={"count": 0})


app.mount("/assets", StaticFiles(directory=ROOT / "front/dist/assets"), name="assets")


@app.get("/{path:path}")
async def spa(path: str):
    if path.startswith(("api/", "user/", "file/")):
        raise HTTPException(404)
    return FileResponse(ROOT / "front/dist/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3081, access_log=False)
