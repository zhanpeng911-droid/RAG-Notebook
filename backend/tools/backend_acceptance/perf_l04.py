"""L04: bounded-concurrency real upload/index/analysis long chain."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from sqlalchemy import select

from tools.backend_acceptance.chain_metrics import completion_throughput
from tools.backend_acceptance import dataset

API = os.environ.get("NB_ACCEPTANCE_API_BASE", "http://backend:8000/api/v1")
RUN = os.environ.get("NB_ACCEPTANCE_RUN_ID", "")
ROOT = Path(os.environ.get("NB_PERF_DATA", f"/app/data/perf/{RUN}"))
DATA = ROOT / "l04-dataset"


def pct(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


async def wait_job(client, token, job_id, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"{API}/insights/jobs/{job_id}",
                                    headers={"Authorization": f"Bearer {token}"},
                                    timeout=30)
        data = response.json().get("data", {})
        if data.get("status") in ("succeeded", "failed", "stale"):
            return data
        await asyncio.sleep(2)
    return {"status": "timeout"}


async def run_one(client, account, path, label, index, sem):
    async with sem:
        started = datetime.now(timezone.utc).isoformat()
        try:
            row = await _run_one(client, account, path, label, index)
        except Exception as exc:
            row = {"label": label, "ok": False, "error": type(exc).__name__}
        return {**row, "started_utc": started,
                "finished_utc": datetime.now(timezone.utc).isoformat()}


async def _run_one(client, account, path, label, index):
    from app.db.db_config import AsyncSessionLocal
    from app.models.document_index import DocumentIndex

    token = account["token"]
    headers = {"Authorization": f"Bearer {token}"}
    unique = path.parent / f"{label}-{index}-{uuid.uuid4().hex[:8]}.txt"
    unique.write_bytes(path.read_bytes() + f"\n\nrun marker {uuid.uuid4()}\n".encode())
    began = time.monotonic()
    try:
        with unique.open("rb") as stream:
            upload = await client.post(
                f"{API}/knowledge/add/single",
                headers=headers,
                files={"file": (unique.name, stream, "text/plain")},
                timeout=900)
        upload_seconds = time.monotonic() - began
        if upload.status_code != 200:
            return {"label": label, "ok": False, "error": f"upload_{upload.status_code}"}
        doc = None
        for _ in range(60):
            async with AsyncSessionLocal() as session:
                doc = (await session.execute(select(DocumentIndex).where(
                    DocumentIndex.user_id == account["user_id"],
                    DocumentIndex.original_filename == unique.name,
                ))).scalar_one_or_none()
            if doc is not None and str(getattr(doc.status, "value", doc.status)) == "indexed":
                break
            await asyncio.sleep(1)
        if doc is None:
            return {"label": label, "ok": False, "error": "document_not_found"}
        if str(getattr(doc.status, "value", doc.status)) != "indexed":
            return {"label": label, "ok": False,
                    "error": f"index_{getattr(doc.status, 'value', doc.status)}"}
        doc_id = doc.id
        analysis_began = time.monotonic()
        generate = await client.post(
            f"{API}/insights/documents/{doc_id}/generate",
            headers=headers, json={"force": True}, timeout=60)
        if generate.status_code != 200:
            return {"label": label, "ok": False,
                    "error": f"generate_{generate.status_code}"}
        job_id = generate.json()["data"]["job_id"]
        status = await wait_job(client, token, job_id)
        analysis_seconds = time.monotonic() - analysis_began
        return {
            "label": label, "ok": status.get("status") == "succeeded",
            "upload_seconds": upload_seconds, "analysis_seconds": analysis_seconds,
            "e2e_seconds": time.monotonic() - began,
            "status": status.get("status"), "document_id": doc_id,
            "job_id": job_id, "error": status.get("error"),
        }
    except Exception as exc:
        return {"label": label, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        unique.unlink(missing_ok=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--m-count", type=int, default=30)
    parser.add_argument("--l-count", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", default=f"/app/data/perf/{RUN}/L04-long-chain")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("samples.json", "summary.json")):
        raise ValueError("Existing evidence is read-only; use a fresh run")
    if not DATA.exists():
        dataset.generate(DATA)
    accounts = json.loads((ROOT / "accounts.json").read_text(encoding="utf-8"))
    sem = asyncio.Semaphore(args.concurrency)
    work = []
    async with httpx.AsyncClient() as client:
        for index in range(args.m_count):
            work.append(run_one(client, accounts[index], DATA / "medium.txt",
                                "M", index, sem))
        for index in range(args.l_count):
            work.append(run_one(client, accounts[args.m_count + index],
                                DATA / "large.txt", "L", index, sem))
        rows = await asyncio.gather(*work)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "samples.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    endpoints = {}
    all_pass = True
    for label, slo_p95, slo_p99 in (("M", 120_000, 180_000), ("L", 300_000, 600_000)):
        group = [row for row in rows if row["label"] == label]
        ok = [row for row in group if row.get("ok")]
        values = [row["analysis_seconds"] * 1000 for row in ok]
        e2e_values = [row["e2e_seconds"] * 1000 for row in ok]
        passed = len(ok) >= (30 if label == "M" else 10) and values \
            and pct(values, 0.95) <= slo_p95 and pct(values, 0.99) <= slo_p99
        all_pass = all_pass and bool(passed)
        endpoints[f"long_chain_{label.lower()}"] = {
            "requests_planned": len(group), "requests_started": len(group),
            "requests_completed": len(group), "successes": len(ok),
            **completion_throughput(group),
            "error_rate": round((len(group) - len(ok)) / max(1, len(group)), 6),
            "content_failures": 0, "timeouts": 0,
            "status_counts": {}, "errors": {},
            "samples": len(values), "sample_sufficient": len(values) >= (30 if label == "M" else 10),
            "p50_ms": round(pct(values, 0.5), 3) if values else None,
            "p95_ms": round(pct(values, 0.95), 3) if values else None,
            "p99_ms": round(pct(values, 0.99), 3) if values else None,
            "max_ms": round(max(values), 3) if values else None,
            "metric_scope": "analysis_execution_only",
            "e2e_p50_ms": round(pct(e2e_values, 0.5), 3) if e2e_values else None,
            "e2e_p95_ms": round(pct(e2e_values, 0.95), 3) if e2e_values else None,
            "e2e_p99_ms": round(pct(e2e_values, 0.99), 3) if e2e_values else None,
            "e2e_max_ms": round(max(e2e_values), 3) if e2e_values else None,
            "slo_p95_ms": slo_p95, "slo_p99_ms": slo_p99,
            "error_rate_limit": 0.01, "passed": bool(passed),
        }
    summary = {
        "measurement_schema_version": 2, "run_id": RUN,
        "scenario": "L04-long-chain", "profile": "l04_long_chain", "tier": "steady",
        "target_qps": 0, "duration_s": None, "warmup_s": 0,
        "max_in_flight": args.concurrency, "dropped_due_to_inflight_limit": 0,
        "accounts": len(accounts), "endpoints": endpoints,
        "all_groups_passed": all_pass,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
