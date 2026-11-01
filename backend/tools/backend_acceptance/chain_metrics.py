"""Pure timing rules for bounded-concurrency chain/SSE evidence."""
from __future__ import annotations

import math


def completion_throughput(rows: list[dict]) -> dict:
    if not rows or any(row.get("started_utc") is None or row.get("finished_utc") is None
                       for row in rows):
        return {"success_qps": None, "throughput_scope": "unverified_missing_wall_times"}
    from datetime import datetime

    spans = []
    for row in rows:
        start = datetime.fromisoformat(row["started_utc"])
        finish = datetime.fromisoformat(row["finished_utc"])
        if start.tzinfo is None or finish.tzinfo is None or finish < start:
            raise ValueError("Invalid chain timestamps")
        spans.append((start, finish))
    duration = (max(end for _, end in spans) - min(start for start, _ in spans)).total_seconds()
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid chain measurement duration")
    return {"success_qps": sum(bool(row.get("ok")) for row in rows) / duration,
            "throughput_scope": "first_dispatch_to_last_completion",
            "wall_duration_s": duration}
