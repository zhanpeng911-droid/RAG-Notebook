"""L08: explicit-answer-token coverage, latency and terminal acceptance."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.backend_acceptance.chain_metrics import completion_throughput  # noqa: E402

API = os.environ.get("NB_ACCEPTANCE_API_BASE", "http://backend:8000/api/v1")
RUN = os.environ.get("NB_ACCEPTANCE_RUN_ID", "")
ROOT = Path(os.environ.get("NB_PERF_DATA", f"/app/data/perf/{RUN}"))


def pct(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def token_text(event):
    if event.get("type") not in ("answer_token", "token", "message_delta"):
        return ""
    delta = event.get("delta")
    return delta if isinstance(delta, str) and delta.strip() else ""


async def one(client, account, sem):
    async with sem:
        headers = {"Authorization": f"Bearer {account['token']}"}
        began = time.perf_counter()
        started_utc = datetime.now(timezone.utc).isoformat()
        first_progress = first_answer = terminal = error = None
        token_count = 0
        try:
            async with client.stream(
                    "POST", f"{API}/chat/agent/query/stream", headers=headers,
                    json={"query": "What is retrieval?"}, timeout=60) as response:
                if response.status_code != 200:
                    error = f"status_{response.status_code}"
                else:
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except ValueError:
                            continue
                        now = time.perf_counter() - began
                        kind = event.get("type")
                        if first_progress is None and kind:
                            first_progress = now
                        if token_text(event):
                            token_count += 1
                            if first_answer is None:
                                first_answer = now
                        if kind in ("completed", "error", "timeout", "cancelled"):
                            terminal = {"type": kind, "seconds": now,
                                        "run_id": event.get("run_id")}
                            break
        except Exception as exc:
            error = type(exc).__name__
        completed = terminal is not None and terminal["type"] == "completed" and not error
        if completed and first_answer is None:
            error = "completed_without_answer_token"
        return {
            "ok": completed and first_answer is not None, "completed": completed,
            "error": error, "first_progress": first_progress, "first_answer": first_answer,
            "answer_token_count": token_count, "terminal": terminal,
            "started_utc": started_utc, "finished_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": time.perf_counter() - began,
        }


async def seed_queryable_note(client, account, sem):
    async with sem:
        response = await client.post(
            f"{API}/note/create",
            headers={"Authorization": f"Bearer {account['token']}"},
            json={"title": f"L08 retrieval seed {account['user_id'][:8]}",
                  "content": "Retrieval finds relevant evidence from stored knowledge.",
                  "tags": ["l08"], "category": "work"}, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"L08 seed rejected: status_{response.status_code}")
        note_id = response.json().get("data", {}).get("id")
        if not note_id:
            raise ValueError("L08 seed response missing note id")
        return {"note_id": note_id, "user_id": account["user_id"]}


async def wait_for_seed_vectors(seeds, *, timeout=180, probe=None):
    if probe is None:
        from app.services.note_vector_index import NoteVectorIndex
        index = NoteVectorIndex()

        def probe():
            result = index.store.get(ids=[row["note_id"] for row in seeds],
                                     include=["metadatas"])
            return {str(nid): metadata for nid, metadata in
                    zip(result.get("ids") or [], result.get("metadatas") or [])}

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            indexed = await asyncio.wait_for(
                asyncio.to_thread(probe),
                timeout=min(10, max(.01, deadline - time.perf_counter())))
        except TimeoutError:
            # Do not let asyncio's empty TimeoutError replace the explicit
            # readiness message that callers/tests assert on.
            if time.perf_counter() >= deadline:
                break
            continue
        missing = [row["note_id"] for row in seeds if not (
            isinstance(indexed.get(row["note_id"]), dict)
            and indexed[row["note_id"]].get("user_id") == row["user_id"]
            and indexed[row["note_id"]].get("note_id") == row["note_id"]
            and indexed[row["note_id"]].get("doc_type") == "note")]
        if not missing:
            return {"passed": True, "expected": len(seeds), "indexed": len(seeds)}
        await asyncio.sleep(min(2, max(0, deadline - time.perf_counter())))
    raise TimeoutError("L08 seeds are not all indexed; measurement was not started")


def summarize(rows, *, accounts=100, concurrency=10):
    progress = [r["first_progress"] * 1000 for r in rows if r.get("first_progress") is not None]
    answer = [r["first_answer"] * 1000 for r in rows
              if r.get("ok") and r.get("first_answer") is not None]
    completed = sum(bool(r.get("completed")) for r in rows)
    successful = sum(bool(r.get("ok")) for r in rows)
    terminal_rate = completed / len(rows) if rows else 0
    coverage = len(answer) / len(rows) if rows else 0
    # Both a full latency sample and population coverage are required. A completed
    # refusal remains a terminal outcome, but cannot disappear from TTFT failures.
    sufficient = len(answer) >= 200
    passed = bool(sufficient and terminal_rate >= .995 and coverage >= .995
                  and pct(answer, .95) <= 3000 and pct(answer, .99) <= 5000)
    return {
        "measurement_schema_version": 2, "run_id": RUN,
        "scenario": "L08-sse", "profile": "l08_sse", "tier": "steady",
        "target_qps": None, "duration_s": None, "warmup_s": 0,
        "max_in_flight": concurrency, "dropped_due_to_inflight_limit": 0,
        "accounts": accounts, "all_groups_passed": passed,
        "endpoints": {"stream": {
            "requests_planned": len(rows), "requests_started": len(rows),
            "requests_completed": len(rows), "successes": successful,
            **completion_throughput(rows), "error_rate": 1 - successful / len(rows) if rows else 1,
            "content_failures": len(rows) - successful,
            "timeouts": sum(r.get("error") in ("TimeoutException", "ReadTimeout") for r in rows),
            "status_counts": {}, "errors": {},
            "samples": len(answer), "sample_sufficient": sufficient,
            "p50_ms": pct(answer, .5), "p95_ms": pct(answer, .95), "p99_ms": pct(answer, .99),
            "max_ms": max(answer) if answer else None,
            "slo_p95_ms": 3000, "slo_p99_ms": 5000, "error_rate_limit": .005,
            "passed": passed,
        }},
        "first_progress_p95_ms": pct(progress, .95),
        "first_answer_metric_available": bool(answer),
        "answer_token_coverage": coverage, "missing_answer_tokens": len(rows) - len(answer),
        "metric_scope": "explicit_answer_token", "terminal_rate": terminal_rate,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--seed-timeout", type=float, default=180)
    parser.add_argument("--output", default=f"/app/data/perf/{RUN}/L08-sse")
    args = parser.parse_args()
    if not RUN.startswith("nb-it-") or os.environ.get("MYSQL_DATABASE") != "nb_" + RUN.replace("-", "_"):
        parser.error("Use the current run's isolated acceptance database")
    if args.requests < 200 or args.concurrency < 1 or args.seed_timeout <= 0:
        parser.error("Require >=200 streams, positive concurrency and seed timeout")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("samples.json", "summary.json", "seed_ready.json")):
        raise ValueError("Existing evidence is read-only; use a fresh run")
    accounts = json.loads((ROOT / "accounts.json").read_text(encoding="utf-8"))
    if len(accounts) < 100 or len({row["user_id"] for row in accounts}) != len(accounts):
        raise ValueError("L08 requires at least 100 distinct seeded accounts")
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        seeds = await asyncio.gather(*[
            seed_queryable_note(client, account, sem) for account in accounts])
        readiness = await wait_for_seed_vectors(seeds, timeout=args.seed_timeout)
        (output / "seed_ready.json").write_text(json.dumps(readiness, indent=2), encoding="utf-8")
        rows = await asyncio.gather(*[
            one(client, accounts[index % len(accounts)], sem) for index in range(args.requests)])
    (output / "samples.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    summary = summarize(rows, accounts=len(accounts), concurrency=args.concurrency)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if summary["all_groups_passed"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
