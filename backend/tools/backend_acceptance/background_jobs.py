"""Fixed-rate background indexing/analysis for the mixed and long-run load."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.db.db_config import AsyncSessionLocal
from app.models.document_index import DocumentIndexStatus
from app.models.knowledge_insight import JobStatus, KnowledgeGenerationJob
from app.models.note import Note
from app.repositories.document_index_repository import DocumentIndexRepository
from app.services.insight_generation_service import queue_summary_generation

RUN = os.environ["NB_ACCEPTANCE_RUN_ID"]
ROOT = Path(os.environ.get("NB_PERF_DATA", f"/app/data/perf/{RUN}"))
BG = ROOT / "background"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def enqueue_index(count: int, counter: int):
    from app.tasks.index_task import index_document_task
    accounts = json.loads((ROOT / "accounts.json").read_text(encoding="utf-8"))
    BG.mkdir(parents=True, exist_ok=True)
    for offset in range(count):
        account = accounts[(counter + offset) % len(accounts)]["user_id"]
        name = f"bg-{counter}-{offset}-{uuid.uuid4().hex[:8]}.txt"
        path = BG / name
        body = ("Background indexing body. " * 40000)[:1_000_000]
        path.write_text(body, encoding="utf-8")
        async with AsyncSessionLocal() as session:
            doc = await DocumentIndexRepository(session).create(
                user_id=account, filename=name, original_filename=name,
                file_path=str(path), md5=hashlib.md5(body.encode()).hexdigest(),
                file_size=path.stat().st_size, file_type="txt",
                status=DocumentIndexStatus.PENDING_INDEX)
            doc_id = doc.id
            await session.commit()
        index_document_task.delay(doc_id, account)


async def enqueue_analysis(count: int, counter: int):
    from app.tasks.insight_task import generate_insight_task
    accounts = json.loads((ROOT / "accounts.json").read_text(encoding="utf-8"))
    for offset in range(count):
        account = accounts[(counter + offset) % len(accounts)]["user_id"]
        async with AsyncSessionLocal() as session:
            note = (await session.execute(select(Note).where(
                Note.user_id == account).limit(1).offset((counter + offset) % 1000)
            )).scalar_one_or_none()
            if note is None:
                continue
            active = (await session.execute(select(func.count()).where(
                KnowledgeGenerationJob.owner_user_id == account,
                KnowledgeGenerationJob.status.in_((JobStatus.QUEUED,
                                                   JobStatus.RUNNING))))).scalar()
            if int(active or 0) >= 20:
                continue
            job, _, _ = await queue_summary_generation(
                session, owner_user_id=account, source_type="note",
                source_id=note.id, force=True)
            await session.commit()
        generate_insight_task.delay(job.id)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=900)
    parser.add_argument("--index-per-minute", type=int, default=2)
    parser.add_argument("--analysis-per-minute", type=int, default=2)
    parser.add_argument("--run-id", default=RUN)
    args = parser.parse_args()
    log_path = ROOT / f"background-{args.run_id}.jsonl"
    started = time.monotonic()
    counter = 0
    while time.monotonic() - started < args.duration:
        record = {"utc": utc_now(), "minute": counter,
                  "index_enqueued": 0, "analysis_enqueued": 0}
        try:
            await enqueue_index(args.index_per_minute, counter)
            record["index_enqueued"] = args.index_per_minute
        except Exception as exc:
            record["index_error"] = f"{type(exc).__name__}: {exc}"[:300]
        try:
            await enqueue_analysis(args.analysis_per_minute, counter)
            record["analysis_enqueued"] = args.analysis_per_minute
        except Exception as exc:
            record["analysis_error"] = f"{type(exc).__name__}: {exc}"[:300]
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        counter += 1
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
