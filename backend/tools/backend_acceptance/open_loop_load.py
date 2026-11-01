"""Open-loop, arrival-rate load generator for the isolated acceptance network.

Concurrency is not used as a proxy for QPS: requests are scheduled at a fixed
arrival interval, bounded in-flight work is recorded, and schedule drift is
kept in requests.csv. Run inside the Compose controller.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import random
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.backend_acceptance.perf_measurement import (  # noqa: E402
    MEASUREMENT_SCHEMA_VERSION, new_group, record_request,
    summarize_group, throughput_checks,
)
from tools.backend_acceptance.queue_probe import observe, wait_for_drain  # noqa: E402

API = os.environ.get("NB_ACCEPTANCE_API_HOST", "http://backend:8000")
PREFIX = "/api/v1"
RUN = os.environ.get("NB_ACCEPTANCE_RUN_ID", "")


def note_id(account: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{account}:note:{index}"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def wait_for_arrival(planned: float) -> float:
    """Return a clock reading at/after the planned arrival, never a clamped one.

    Windows timers can wake before the requested delay. Retrying without a
    fixed attempt cap is what keeps dispatch time honest; clamping the
    reported start while sending early can make a fast response finish
    before the timestamp it is counted at.
    """
    while True:
        now = time.perf_counter()
        if now >= planned:
            return now
        await asyncio.sleep(planned - now)


class Scenario:
    def __init__(self, profile: dict, key: str, accounts: list[dict],
                 documents: dict[str, str], link_count: int = 20):
        self.profile = profile
        self.weights = profile["weights"][key]
        self.accounts = accounts
        self.documents = documents
        self.link_count = link_count
        self.names = list(self.weights)
        self.choices = [self.weights[name] for name in self.names]

    def choose(self, rng: random.Random) -> str:
        return rng.choices(self.names, weights=self.choices)[0]

    def request(self, name: str, account_index: int, counter: int):
        account = self.accounts[account_index]
        uid = account["user_id"]
        note_index = (counter // len(self.accounts)) % 1000
        nid = note_id(uid, note_index)
        doc = self.documents.get(uid)
        headers = {"Authorization": f"Bearer {account['token']}"}
        if name == "list":
            return ("GET", f"{PREFIX}/note/list?page=1&page_size=20", None,
                    "list", headers, nid)
        if name == "detail":
            return "GET", f"{PREFIX}/note/{nid}", None, "detail", headers, nid
        if name == "graph_global":
            return ("GET", f"{PREFIX}/notes/graph?limit=300&include_orphans=true", None,
                    "graph_global", headers, nid)
        if name == "graph_local":
            return ("GET", f"{PREFIX}/notes/{nid}/local-graph?depth=2&limit=200", None,
                    "graph_local", headers, nid)
        if name in ("state", "analysis_state"):
            if not doc:
                raise ValueError("Missing seeded document: state probe cannot use health/live")
            return ("GET", f"{PREFIX}/insights/sources/document/{doc}/state", None,
                    "state", headers, nid)
        if name == "candidates":
            return ("GET", f"{PREFIX}/notes/link-candidates?q=Performance&limit=10", None,
                    "candidates", headers, nid)
        if name.startswith("save_"):
            count = int(name.split("_")[1])
            targets = " ".join(
                f"[[note:{note_id(uid, (note_index + offset + 1) % 1000)}|Perf target {offset}]]"
                for offset in range(count))
            body = {
                "title": f"Perf save {RUN} {counter}",
                "content": ("Generated save body. " * 80) + targets,
                "tags": ["perf"], "category": "work",
            }
            group = f"save_{count}"
            return "POST", f"{PREFIX}/note/create", body, group, headers, nid
        raise ValueError(f"unknown endpoint {name}")


def check(group: str, response: httpx.Response, expected_note: str) -> bool:
    if response.status_code != 200:
        return False
    try:
        data = response.json().get("data")
    except ValueError:
        return False
    if group == "list":
        return isinstance(data, dict) and isinstance(data.get("notes"), list)
    if group == "detail":
        return isinstance(data, dict) and data.get("id") == expected_note
    if group in ("graph_global", "graph_local"):
        nodes = data.get("nodes") if isinstance(data, dict) else None
        return isinstance(nodes, list) and len(nodes) > 0
    if group == "state":
        return isinstance(data, dict) and "status" in data
    if group == "candidates":
        return isinstance(data, dict) and isinstance(data.get("candidates"), list)
    if group.startswith("save_"):
        return isinstance(data, dict) and isinstance(data.get("id"), str)
    return True


async def run(args) -> dict:
    profile_bytes = Path(args.profile).read_bytes()
    profile = json.loads(profile_bytes)
    accounts = json.loads(Path(args.accounts).read_text(encoding="utf-8"))
    documents = {}
    if Path(args.documents).is_file():
        for row in json.loads(Path(args.documents).read_text(encoding="utf-8")):
            documents[row["account"]] = row["document_id"]
    if not accounts or len({row["user_id"] for row in accounts}) != len(accounts):
        raise ValueError("Accounts must be nonempty and unique")
    scenario = Scenario(profile, args.weights, accounts, documents, args.link_count)
    if any(name in scenario.names for name in ("state", "analysis_state")):
        if any(account["user_id"] not in documents for account in accounts):
            raise ValueError("State workload requires a seeded document for every account")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / args.qps
    total = math.ceil((args.warmup + args.duration) * args.qps)
    origin = time.perf_counter()
    wall_origin = datetime.now(timezone.utc)
    sample_start, sample_end = origin + args.warmup, origin + args.warmup + args.duration

    def wall(stamp):
        return (wall_origin + timedelta(seconds=stamp - origin)).isoformat()

    stats = defaultdict(new_group)
    for name in scenario.names:
        stats["state" if name == "analysis_state" else name]
    dropped, warmup_dropped = 0, 0
    failures: list[BaseException] = []
    in_flight: set[asyncio.Task] = set()
    rng = random.Random(args.seed)
    fields = (
        "measurement_schema_version", "run_id", "scenario", "endpoint",
        "account_index", "planned_utc", "started_utc", "finished_utc",
        "planned_in_window", "started_in_window", "finished_in_window",
        "schedule_delay_ms", "service_ms", "elapsed_ms", "status", "error", "content_ok",
    )
    with (output / "requests.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        limits = httpx.Limits(max_connections=args.max_inflight,
                              max_keepalive_connections=args.max_inflight)
        async with httpx.AsyncClient(base_url=API, timeout=args.timeout,
                                     limits=limits, follow_redirects=False) as client:
            async def execute(planned, account_index, request):
                nonlocal dropped, warmup_dropped
                method, path, body, group, headers, expected = request
                began = None
                status, error, content_ok = None, "", False
                try:
                    began = await wait_for_arrival(planned)
                    response = await client.request(method, path, json=body, headers=headers)
                    status = response.status_code
                    content_ok = check(group, response, expected)
                except asyncio.CancelledError:
                    error = "not_dispatched" if began is None else "drain_timeout"
                    raise
                except httpx.TimeoutException:
                    error = "timeout"
                except Exception as exc:
                    error = type(exc).__name__
                finally:
                    finished = time.perf_counter()
                    measured = sample_start <= planned < sample_end
                    if began is None:
                        # A queued arrival cancelled before dispatch is still an
                        # arrival: account for it as dropped, never lose its row.
                        dropped += int(measured)
                        warmup_dropped += int(not measured)
                        stats[group]["dropped"] += int(measured)
                    else:
                        record_request(
                            stats[group], planned=planned, started=began, finished=finished,
                            sample_start=sample_start, sample_end=sample_end,
                            status=status, error=error, content_ok=content_ok,
                        )
                    writer.writerow({
                        "measurement_schema_version": MEASUREMENT_SCHEMA_VERSION,
                        "run_id": RUN, "scenario": args.scenario, "endpoint": group,
                        "account_index": account_index, "planned_utc": wall(planned),
                        "started_utc": wall(began) if began is not None else "",
                        "finished_utc": wall(finished),
                        "planned_in_window": measured,
                        "started_in_window": (
                            began is not None and sample_start <= began < sample_end),
                        "finished_in_window": sample_start <= finished < sample_end,
                        "schedule_delay_ms": (
                            (began - planned) * 1000 if began is not None else ""),
                        "service_ms": (
                            (finished - began) * 1000 if began is not None else ""),
                        "elapsed_ms": (
                            (finished - planned) * 1000 if began is not None else ""),
                        "status": status, "error": error, "content_ok": content_ok,
                    })

            def task_done(task):
                in_flight.discard(task)
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    failures.append(exc)

            for counter in range(total):
                planned = origin + counter * interval
                if planned >= sample_end:
                    break
                delay = planned - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                account_index = counter % len(accounts)
                # Choose before capacity check so dropped arrivals retain endpoint identity.
                request = scenario.request(scenario.choose(rng), account_index, counter)
                group = request[3]
                measured = sample_start <= planned < sample_end
                stats[group]["planned"] += int(measured)
                if len(in_flight) >= args.max_inflight:
                    dropped += int(measured)
                    warmup_dropped += int(not measured)
                    stats[group]["dropped"] += int(measured)
                    writer.writerow({
                        "measurement_schema_version": MEASUREMENT_SCHEMA_VERSION,
                        "run_id": RUN, "scenario": args.scenario, "endpoint": group,
                        "account_index": account_index, "planned_utc": wall(planned),
                        "planned_in_window": measured, "error": "inflight_limit",
                    })
                    continue
                task = asyncio.create_task(execute(planned, account_index, request))
                in_flight.add(task)
                task.add_done_callback(task_done)
            await asyncio.sleep(max(0, sample_end - time.perf_counter()))
            if in_flight:
                _, pending = await asyncio.wait(list(in_flight), timeout=args.timeout + 5)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            if failures:
                raise RuntimeError(
                    f"{len(failures)} load-generator task(s) failed; first: {failures[0]!r}"
                ) from failures[0]

    groups = profile["targets"]["groups"]
    minimum = profile["targets"][
        "long_run_min_samples_per_endpoint" if args.long_run
        else "steady_min_samples_per_endpoint"]
    error_limit = profile["targets"][
        "steady_error_rate" if args.tier == "steady" else "peak_error_rate"]
    summary = {
        group: summarize_group(row, target=groups[group], error_limit=error_limit,
                               min_samples=minimum, duration=args.duration, tier=args.tier)
        for group, row in stats.items()
    }
    checks = throughput_checks(
        target_qps=args.qps, started=sum(row["started"] for row in stats.values()),
        successes_in_window=sum(row["success_in_window"] for row in stats.values()),
        duration=args.duration, dropped=dropped,
    )
    return {
        "measurement_schema_version": MEASUREMENT_SCHEMA_VERSION, "run_id": RUN,
        "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "scenario": args.scenario, "profile": args.weights, "tier": args.tier,
        "target_qps": args.qps, "duration_s": args.duration, "warmup_s": args.warmup,
        "measurement_start_utc": wall(sample_start), "measurement_end_utc": wall(sample_end),
        "clock": "perf_counter", "max_in_flight": args.max_inflight,
        "dropped_due_to_inflight_limit": dropped, "warmup_dropped": warmup_dropped,
        **checks, "target_achieved": checks["arrival_ok"],
        "accounts": len(accounts), "endpoints": summary,
        "all_groups_passed": checks["throughput_ok"] and all(
            row["passed"] for row in summary.values()),
        "boundary_observation": args.tier == "boundary", "generated_utc": utc_now(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--profile",
                        default=str(Path(__file__).resolve().parent / "perf_profile.json"))
    parser.add_argument("--weights", required=True,
                        choices=["l01_reads", "l03_mixed", "l02_save",
                                 "l02_save_20", "l02_save_100"])
    parser.add_argument("--accounts")
    parser.add_argument("--documents")
    parser.add_argument("--output", required=True)
    parser.add_argument("--qps", type=float, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--warmup", type=float, default=120)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--max-inflight", type=int, default=400)
    parser.add_argument("--link-count", type=int, default=20)
    parser.add_argument("--tier", choices=["steady", "peak", "boundary"], default="steady")
    parser.add_argument("--long-run", action="store_true")
    parser.add_argument("--observe-db", action="store_true")
    parser.add_argument("--drain-timeout", type=float, default=900)
    parser.add_argument("--seed", type=int, default=20261027)
    args = parser.parse_args()
    if not RUN.startswith("nb-it-"):
        parser.error("NB_ACCEPTANCE_RUN_ID must identify an isolated nb-it- run")
    if any(not math.isfinite(v) or v <= 0 for v in
           (args.qps, args.duration, args.timeout, args.drain_timeout)):
        parser.error("QPS, duration and timeouts must be positive finite numbers")
    if not math.isfinite(args.warmup) or args.warmup < 0 or args.max_inflight < 1:
        parser.error("Invalid warmup or max-inflight")
    if args.long_run and not args.observe_db:
        parser.error("Long-run acceptance requires queue evidence")
    root = Path(os.environ.get("NB_PERF_DATA", f"/app/data/perf/{RUN}"))
    args.accounts = args.accounts or str(root / "accounts.json")
    args.documents = args.documents or str(root / "documents.json")

    async def entry():
        stop = asyncio.Event()
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        if any((output / name).exists() for name in
               ("requests.csv", "summary.json", "queue.csv", "drain.json")):
            raise ValueError("Existing evidence is read-only; use a new run directory")
        observer = asyncio.create_task(observe(output, stop)) if args.observe_db else None
        try:
            result = await run(args)
            if args.observe_db:
                result["queue_drain"] = await wait_for_drain(output, timeout=args.drain_timeout)
                result["all_groups_passed"] &= result["queue_drain"]["passed"]
        finally:
            stop.set()
            if observer:
                await observer
        (output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        observed_boundary = args.tier == "boundary" and any(
            row["requests_started"] for row in result["endpoints"].values())
        raise SystemExit(0 if result["all_groups_passed"] or observed_boundary else 1)
    asyncio.run(entry())


if __name__ == "__main__":
    main()
