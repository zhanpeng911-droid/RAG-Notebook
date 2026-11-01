"""Read-only queue evidence for an explicitly isolated acceptance database."""
from __future__ import annotations

import asyncio
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

PENDING_KEYS = (
    "document_pending_index", "document_indexing", "job_queued", "job_running",
    "note_pending", "note_running", "note_failed",
    "cleanup_pending", "cleanup_claimed", "cleanup_failed", "execution_writing",
)
FAILURE_KEYS = (
    "document_index_failed", "job_failed", "note_blocked", "cleanup_blocked",
    "execution_unknown",
)
COMPLETED_KEYS = ("document_indexed", "job_succeeded", "note_done", "cleanup_done", "execution_closed")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def snapshot() -> dict:
    from sqlalchemy import func, select, text

    from app.config.validator import get_settings
    from app.db.db_config import AsyncSessionLocal
    from app.models.cleanup_task import DocumentCleanupTask
    from app.models.document_index import DocumentIndex
    from app.models.index_execution import DocumentIndexExecution
    from app.models.knowledge_insight import KnowledgeGenerationJob
    from app.models.note_vector_task import NoteVectorTask

    run_id = os.environ.get("NB_ACCEPTANCE_RUN_ID", "")
    expected = "nb_" + run_id.replace("-", "_")
    if not run_id.startswith("nb-it-") or get_settings().MYSQL_DATABASE != expected:
        raise ValueError("Queue probe requires the current run's isolated MySQL database")
    counts = {key: 0 for key in (*PENDING_KEYS, *FAILURE_KEYS, *COMPLETED_KEYS)}
    async with AsyncSessionLocal() as db:
        for prefix, model in (
            ("document", DocumentIndex), ("job", KnowledgeGenerationJob),
            ("note", NoteVectorTask), ("cleanup", DocumentCleanupTask),
            ("execution", DocumentIndexExecution),
        ):
            result = await db.execute(select(model.status, func.count()).group_by(model.status))
            for state, count in result.all():
                counts[f"{prefix}_{getattr(state, 'value', state)}"] = int(count)
        rows = (await db.execute(text(
            "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected', 'Threads_running')"
        ))).all()
        counts.update({str(name).lower(): int(value) for name, value in rows})
    return {"utc": utc_now(), "schema_version": 2, "run_id": run_id, **counts}


async def observe(output: Path, stop: asyncio.Event) -> None:
    # An old CSV is never appended with a different schema or silently overwritten.
    fields = ["schema_version", "run_id", "scenario", "utc", *PENDING_KEYS, *FAILURE_KEYS, *COMPLETED_KEYS,
              "threads_connected", "threads_running", "error"]
    with (output / "queue.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        while not stop.is_set():
            try:
                row = await asyncio.wait_for(snapshot(), timeout=10)
            except Exception as exc:
                row = {"utc": utc_now(), "error": type(exc).__name__}
            writer.writerow({"schema_version": 2,
                             "run_id": os.environ.get("NB_ACCEPTANCE_RUN_ID", ""),
                             "scenario": output.name, **row})
            stream.flush()
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except TimeoutError:
                pass


async def wait_for_drain(output: Path, *, timeout: float = 900) -> dict:
    """Two consecutive empty observations; unknown/blocked is never called drained."""
    began = time.monotonic()
    deadline = began + timeout
    result = {"measurement_schema_version": 2,
              "run_id": os.environ.get("NB_ACCEPTANCE_RUN_ID", ""),
              "scenario": output.name, "began_utc": utc_now(),
              "timeout_s": timeout, "passed": False, "snapshots": []}
    empty = 0
    while time.monotonic() < deadline:
        try:
            row = await asyncio.wait_for(snapshot(), timeout=min(10, deadline - time.monotonic()))
        except Exception as exc:
            result["reason"] = f"queue_probe_{type(exc).__name__}"
            break
        result["snapshots"].append(row)
        if any(row[key] for key in FAILURE_KEYS):
            result["reason"] = "non_fault_load_left_failed_unknown_or_blocked_work"
            break
        empty = empty + 1 if not any(row[key] for key in PENDING_KEYS) else 0
        if empty >= 2:
            result["passed"] = True
            break
        await asyncio.sleep(min(10, max(0, deadline - time.monotonic())))
    result.setdefault("reason", "drained" if result["passed"] else "drain_timeout")
    result.update(ended_utc=utc_now(), elapsed_s=time.monotonic() - began)
    with (output / "drain.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result
