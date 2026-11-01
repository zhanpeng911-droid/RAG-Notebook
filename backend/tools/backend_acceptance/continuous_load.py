"""L07: one HTTP client, one clock and one in-flight pool for all three rates."""
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
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.backend_acceptance.open_loop_load import API, Scenario, check, wait_for_arrival  # noqa: E402
from tools.backend_acceptance.perf_measurement import (  # noqa: E402
    new_group, record_request, summarize_group, throughput_checks,
)
from tools.backend_acceptance.queue_probe import observe, wait_for_drain  # noqa: E402

PHASES = (("L07-steady", 50, 300, "steady"),
          ("L07-peak", 100, 600, "peak"),
          ("L07-recovery", 50, 600, "steady"))
FIELDS = ("measurement_schema_version", "run_id", "scenario", "endpoint", "account_index",
          "planned_utc", "started_utc", "finished_utc", "planned_in_window", "started_in_window",
          "finished_in_window", "schedule_delay_ms", "service_ms", "elapsed_ms", "status",
          "error", "content_ok")


def phase_windows(origin, warmup, phases=PHASES):
    windows = []
    for index, (name, qps, duration, tier) in enumerate(phases):
        before = warmup if index == 0 else 0
        windows.append({"name": name, "qps": qps, "duration": duration, "tier": tier,
                        "origin": origin, "warmup": before, "start": origin + before,
                        "end": origin + before + duration})
        origin = windows[-1]["end"]
    return windows


async def run_chain(args, phases=PHASES):
    run_id = os.environ["NB_ACCEPTANCE_RUN_ID"]
    profile_bytes = Path(args.profile).read_bytes()
    profile = json.loads(profile_bytes)
    root = Path(os.environ["NB_PERF_DATA"])
    accounts = json.loads((root / "accounts.json").read_text(encoding="utf-8"))
    documents = {row["account"]: row["document_id"] for row in
                 json.loads((root / "documents.json").read_text(encoding="utf-8"))}
    if len(accounts) < profile["accounts"] or any(row["user_id"] not in documents for row in accounts):
        raise ValueError("L07 requires the full seeded account/document workload")
    scenario = Scenario(profile, "l03_mixed", accounts, documents)
    output = Path(args.output)
    chain_dir = output / "L07-chain"
    chain_dir.mkdir(parents=True, exist_ok=True)
    if any((chain_dir / name).exists() for name in ("chain.json", "queue.csv", "drain.json")):
        raise ValueError("Existing chain evidence is read-only")
    for name, _, _, _ in phases:
        target = output / name
        if target.exists() and any(target.iterdir()):
            raise ValueError("Existing phase evidence is read-only")
        target.mkdir(parents=True, exist_ok=True)
    chain_id = str(uuid.uuid4())
    rng = random.Random(args.seed)
    active = set()
    stats = {name: {group: new_group() for group in scenario.names} for name, _, _, _ in phases}
    stop = asyncio.Event()
    observer = asyncio.create_task(observe(chain_dir, stop))
    try:
        with ExitStack() as stack:
            writers = {}
            for name, _, _, _ in phases:
                stream = stack.enter_context((output / name / "requests.csv").open(
                    "x", newline="", encoding="utf-8"))
                writers[name] = csv.DictWriter(stream, fieldnames=FIELDS)
                writers[name].writeheader()
            limits = httpx.Limits(max_connections=args.max_inflight,
                                  max_keepalive_connections=args.max_inflight)
            async with httpx.AsyncClient(base_url=API, timeout=args.timeout, limits=limits) as client:
                origin, wall_origin = time.perf_counter(), datetime.now(timezone.utc)
                windows = phase_windows(origin, args.warmup, phases)

                def wall(stamp):
                    return (wall_origin + timedelta(seconds=stamp - origin)).isoformat()

                async def execute(window, planned, account, request):
                    method, path, body, group, headers, expected = request
                    began, status, error, valid = None, None, "", False
                    try:
                        began = await wait_for_arrival(planned)
                        response = await client.request(method, path, json=body, headers=headers)
                        status, valid = response.status_code, check(group, response, expected)
                    except asyncio.CancelledError:
                        error = "drain_timeout" if began is not None else "not_dispatched"
                        raise
                    except httpx.TimeoutException:
                        error = "timeout"
                    except Exception as exc:
                        error = type(exc).__name__
                    finally:
                        end = time.perf_counter()
                        measured = window["start"] <= planned < window["end"]
                        if began is not None:
                            record_request(stats[window["name"]][group], planned=planned,
                                           started=began, finished=end, sample_start=window["start"],
                                           sample_end=window["end"], status=status,
                                           error=error, content_ok=valid)
                        elif measured:
                            stats[window["name"]][group]["dropped"] += 1
                        writers[window["name"]].writerow({
                            "measurement_schema_version": 2, "run_id": run_id,
                            "scenario": window["name"], "endpoint": group, "account_index": account,
                            "planned_utc": wall(planned), "started_utc": wall(began) if began else "",
                            "finished_utc": wall(end), "planned_in_window": measured,
                            "started_in_window": began is not None and window["start"] <= began < window["end"],
                            "finished_in_window": window["start"] <= end < window["end"],
                            "schedule_delay_ms": (began - planned) * 1000 if began else "",
                            "service_ms": (end - began) * 1000 if began else "",
                            "elapsed_ms": (end - planned) * 1000 if began else "",
                            "status": status, "error": error, "content_ok": valid,
                        })

                counter = 0
                for window in windows:
                    # No drain, sleep-for-recovery, file rotation or HTTP client
                    # construction occurs at a phase boundary.
                    count = math.ceil((window["warmup"] + window["duration"]) * window["qps"])
                    for index in range(count):
                        planned = window["origin"] + index / window["qps"]
                        if planned >= window["end"]:
                            break
                        await wait_for_arrival(planned)
                        account = counter % len(accounts)
                        request = scenario.request(scenario.choose(rng), account, counter)
                        counter += 1
                        group = request[3]
                        measured = window["start"] <= planned < window["end"]
                        row = stats[window["name"]][group]
                        row["planned"] += int(measured)
                        if len(active) >= args.max_inflight:
                            row["dropped"] += int(measured)
                            writers[window["name"]].writerow({
                                "measurement_schema_version": 2, "run_id": run_id,
                                "scenario": window["name"], "endpoint": group,
                                "account_index": account, "planned_utc": wall(planned),
                                "planned_in_window": measured, "error": "inflight_limit"})
                            continue
                        task = asyncio.create_task(execute(window, planned, account, request))
                        active.add(task)
                        task.add_done_callback(active.discard)
                await wait_for_arrival(windows[-1]["end"])
                if active:
                    _, pending = await asyncio.wait(list(active), timeout=args.timeout + 5)
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
        drain = await wait_for_drain(chain_dir, timeout=900)
    finally:
        stop.set()
        await observer
    summaries = []
    for index, window in enumerate(windows):
        tier = window["tier"]
        endpoints = {group: summarize_group(
            row, target=profile["targets"]["groups"][group],
            error_limit=profile["targets"][f"{tier}_error_rate"],
            min_samples=profile["targets"]["steady_min_samples_per_endpoint"],
            duration=window["duration"], tier=tier)
            for group, row in stats[window["name"]].items()}
        checks = throughput_checks(
            target_qps=window["qps"], started=sum(row["requests_started"] for row in endpoints.values()),
            successes_in_window=sum(row["successes_in_window"] for row in endpoints.values()),
            duration=window["duration"], dropped=sum(row["requests_dropped"] for row in endpoints.values()))
        summary = {
            "measurement_schema_version": 2, "run_id": run_id,
            "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
            "scenario": window["name"], "profile": "l03_mixed", "tier": tier,
            "target_qps": window["qps"], "duration_s": window["duration"], "warmup_s": window["warmup"],
            "measurement_start_utc": wall(window["start"]), "measurement_end_utc": wall(window["end"]),
            "phase_chain_id": chain_id, "phase_sequence_index": index,
            "dropped_due_to_inflight_limit": sum(row["requests_dropped"] for row in endpoints.values()),
            "endpoints": endpoints, **checks,
            "all_groups_passed": checks["throughput_ok"] and drain["passed"]
            and all(row["passed"] for row in endpoints.values()),
        }
        (output / window["name"] / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        summaries.append(summary)
    (chain_dir / "chain.json").write_text(json.dumps({
        "run_id": run_id, "phase_chain_id": chain_id, "shared_client": True,
        "shared_inflight_pool": True, "phases": [row["scenario"] for row in summaries],
    }, indent=2), encoding="utf-8")
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Fresh perf directory")
    parser.add_argument("--profile", default=str(Path(__file__).with_name("perf_profile.json")))
    parser.add_argument("--warmup", type=float, default=120)
    parser.add_argument("--max-inflight", type=int, default=400)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--seed", type=int, default=20261027)
    args = parser.parse_args()
    run_id = os.environ.get("NB_ACCEPTANCE_RUN_ID", "")
    if not run_id.startswith("nb-it-") or os.environ.get("MYSQL_DATABASE") != "nb_" + run_id.replace("-", "_"):
        parser.error("Use the current run's isolated acceptance database")
    if args.warmup < 0 or args.timeout <= 0 or args.max_inflight < 1:
        parser.error("Invalid timing/capacity settings")
    summaries = asyncio.run(run_chain(args))
    raise SystemExit(0 if all(row["all_groups_passed"] for row in summaries) else 1)


if __name__ == "__main__":
    main()
