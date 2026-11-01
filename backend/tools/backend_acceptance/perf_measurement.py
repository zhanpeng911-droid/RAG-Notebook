"""Pure measurement rules; no settings, services, clock reads or file writes."""
from __future__ import annotations

import math
from collections import Counter

MEASUREMENT_SCHEMA_VERSION = 2
ARRIVAL_TOLERANCE = 0.02
MIN_SUCCESS_RATIO = 0.98


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def throughput_checks(*, target_qps: float, started: int, successes_in_window: int,
                      duration: float, dropped: int) -> dict:
    if not math.isfinite(target_qps) or not math.isfinite(duration):
        raise ValueError("QPS and duration must be finite")
    if target_qps <= 0 or duration <= 0 or min(started, successes_in_window, dropped) < 0:
        raise ValueError("Invalid throughput counters")
    actual = started / duration
    success = successes_in_window / duration
    arrival_ok = abs(actual - target_qps) <= target_qps * ARRIVAL_TOLERANCE + 1e-9
    success_ok = success + 1e-9 >= target_qps * MIN_SUCCESS_RATIO
    return {
        "started_qps": actual,
        "success_qps": success,
        "arrival_deviation_ratio": abs(actual / target_qps - 1),
        "arrival_ok": arrival_ok,
        "success_throughput_ok": success_ok,
        "drop_ok": dropped == 0,
        "throughput_ok": arrival_ok and success_ok and dropped == 0,
    }


def new_group() -> dict:
    return {
        "planned": 0, "dropped": 0, "started": 0, "completed": 0,
        "completed_in_window": 0, "success": 0, "success_in_window": 0,
        "late_starts": 0, "content_fail": 0, "timeout": 0,
        "status": Counter(), "errors": Counter(), "latencies": [],
        "service_latencies": [], "all_latencies": [],
    }


def record_request(row: dict, *, planned: float, started: float, finished: float,
                   sample_start: float, sample_end: float, status: int | None,
                   error: str, content_ok: bool) -> None:
    """Count actual starts/finishes, not the time an async task was scheduled.

    The actual-start cohort supplies error/latency samples, including its drained
    outcomes. Only successes completed within the window contribute to QPS.
    Warm-up starts that finish in the window never inflate successful throughput.
    """
    if not all(math.isfinite(v) for v in (planned, started, finished)):
        raise ValueError("Invalid request timestamps")
    if finished < started or started < planned:
        raise ValueError(
            "Request timestamps are out of order: "
            f"planned={planned:.9f} started={started:.9f} finished={finished:.9f}")
    if sample_start <= planned < sample_end and started >= sample_end:
        row["late_starts"] += 1
    if not sample_start <= started < sample_end:
        return
    row["started"] += 1
    row["completed"] += 1
    row["status"][str(status) if status is not None else "no_response"] += 1
    elapsed = (finished - planned) * 1000
    row["all_latencies"].append(elapsed)
    completed_in_window = finished < sample_end
    row["completed_in_window"] += int(completed_in_window)
    if error:
        row["errors"][error] += 1
        row["timeout"] += int(error in {"timeout", "drain_timeout"})
    elif status is not None and 200 <= status < 300 and content_ok:
        row["success"] += 1
        row["success_in_window"] += int(completed_in_window)
        row["latencies"].append(elapsed)
        row["service_latencies"].append((finished - started) * 1000)
    else:
        row["content_fail"] += 1


def summarize_group(row: dict, *, target: dict, error_limit: float,
                    min_samples: int, duration: float, tier: str) -> dict:
    attempts = row["started"]
    errors = attempts - row["success"]
    error_rate = errors / attempts if attempts else 1.0
    key = "steady" if tier == "steady" else "peak"
    p95_limit = target[f"{key}_p95_ms"]
    p99_limit = target[f"{key}_p99_ms"]
    latencies = row["latencies"]
    p95, p99 = percentile(latencies, .95), percentile(latencies, .99)
    enough = len(latencies) >= min_samples
    slo_passed = bool(
        enough and p95 is not None and p99 is not None
        and p95 <= p95_limit and p99 <= p99_limit
        and error_rate <= error_limit and row["status"].get("429", 0) == 0
    )
    return {
        "requests_planned": row["planned"], "requests_dropped": row["dropped"],
        "requests_started": attempts, "requests_completed": row["completed"],
        "completed_in_window": row["completed_in_window"],
        "completed_after_window": row["completed"] - row["completed_in_window"],
        "late_starts": row["late_starts"], "successes": row["success"],
        "successes_in_window": row["success_in_window"],
        "success_qps": row["success_in_window"] / duration,
        "started_qps": attempts / duration, "error_rate": error_rate,
        "content_failures": row["content_fail"], "timeouts": row["timeout"],
        "status_counts": dict(row["status"]), "errors": dict(row["errors"]),
        "samples": len(latencies), "sample_sufficient": enough,
        "metric_scope": "planned_to_finished_actual_start_cohort",
        "p50_ms": percentile(latencies, .5), "p95_ms": p95, "p99_ms": p99,
        "max_ms": max(latencies) if latencies else None,
        "service_p95_ms": percentile(row["service_latencies"], .95),
        "service_p99_ms": percentile(row["service_latencies"], .99),
        "all_results_p95_ms": percentile(row["all_latencies"], .95),
        "all_results_p99_ms": percentile(row["all_latencies"], .99),
        "slo_p95_ms": p95_limit, "slo_p99_ms": p99_limit,
        "error_rate_limit": error_limit,
        "passed": slo_passed and tier != "boundary",
        "boundary_observation": tier == "boundary",
    }
