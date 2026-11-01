"""Closed-loop HTTP sampler for the isolated Compose network only.

The JSON input lists endpoints with name, method, path, weight and optional json.
The caller must seed the synthetic data and supply NB_ACCEPTANCE_TOKEN separately.
"""
import argparse
import asyncio
import csv
import json
import math
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


async def sample(config: dict, output: Path, concurrency: int, duration: float,
                 warmup: float, timeout: float) -> None:
    if not re.fullmatch(r"nb-it-[a-z0-9-]+", os.environ.get("NB_ACCEPTANCE_RUN_ID", "")):
        raise ValueError("NB_ACCEPTANCE_RUN_ID must identify an isolated acceptance run")
    endpoints = config["endpoints"]
    if not endpoints or any(not e["path"].startswith("/api/") or
                            e["path"].startswith("//") or
                            e.get("weight", 1) <= 0 for e in endpoints):
        raise ValueError("only weighted relative /api/ paths are accepted")
    token = os.environ.get("NB_ACCEPTANCE_TOKEN")
    if not token:
        raise ValueError("NB_ACCEPTANCE_TOKEN is required")
    if concurrency < 1 or duration <= 0 or warmup < 0 or timeout <= 0:
        raise ValueError("invalid load parameters")
    output.mkdir(parents=True, exist_ok=False)
    headers = {"Authorization": f"Bearer {token}"}
    rows = []
    start = time.monotonic()
    stop = start + warmup + duration

    async with httpx.AsyncClient(base_url="http://backend:8000", headers=headers,
                                 timeout=timeout, follow_redirects=False) as client:
        async def worker(index: int) -> None:
            rng = random.Random(20261026 + index)
            while time.monotonic() < stop:
                endpoint = rng.choices(endpoints, weights=[e.get("weight", 1) for e in endpoints])[0]
                began = time.monotonic()
                stamp = datetime.now(timezone.utc).isoformat()
                status, error = "", ""
                try:
                    response = await client.request(endpoint.get("method", "GET"), endpoint["path"],
                                                    json=endpoint.get("json"))
                    status = response.status_code
                except httpx.TimeoutException:
                    error = "timeout"
                except httpx.RequestError:
                    error = "request_error"
                finished = time.monotonic()
                elapsed = (finished - began) * 1000
                if began >= start + warmup:
                    rows.append((endpoint["name"], concurrency, stamp, round(elapsed, 3), status,
                                 error, finished <= stop))

        await asyncio.gather(*(worker(i) for i in range(concurrency)))
    with (output / "requests.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("endpoint", "concurrency", "started_utc", "elapsed_ms", "status", "error",
                         "completed_in_window"))
        writer.writerows(rows)
    groups = defaultdict(list)
    for row in rows:
        if row[6]:
            groups[row[0]].append(row)
    summary = {}
    for name, group in groups.items():
        successful = [r[3] for r in group if isinstance(r[4], int) and 200 <= r[4] < 300]
        summary[name] = {"requests": len(group), "successes": len(successful),
                         "success_per_second": len(successful) / duration,
                         "status_counts": {str(s): sum(r[4] == s for r in group)
                                           for s in sorted({r[4] for r in group}, key=str)},
                         "errors": {e: sum(r[5] == e for r in group)
                                    for e in sorted({r[5] for r in group if r[5]})},
                         "p99_sample_sufficient": len(successful) >= 1000}
        if successful:
            summary[name].update({f"p{int(p * 100)}_ms": percentile(successful, p)
                                  for p in (0.5, 0.95, 0.99)})
            summary[name]["max_ms"] = max(successful)
    (output / "summary.json").write_text(json.dumps({"duration_s": duration,
        "completed_after_window": sum(not r[6] for r in rows),
        "warmup_s": warmup, "concurrency": concurrency, "endpoints": summary}, indent=2),
        encoding="utf-8")
    allowed = {e["name"]: e.get("expected_statuses", [200]) for e in endpoints}
    if not rows or any(r[5] or r[4] not in allowed[r[0]] for r in rows):
        raise RuntimeError("No samples or unexpected HTTP failures; inspect requests.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--concurrency", type=int, choices=(1, 5, 10), required=True)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--warmup", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    asyncio.run(sample(json.loads(args.config.read_text(encoding="utf-8")), args.output,
                       args.concurrency, args.duration, args.warmup, args.timeout))
