"""Pure, fail-closed resource acceptance rules for schema-2 evidence."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from statistics import median

from tools.backend_acceptance.queue_probe import FAILURE_KEYS, PENDING_KEYS

DEFAULT_LIMITS = {
    "max_sample_gap_s": 15, "comparison_window_s": 600,
    "rss_growth_ratio": .10, "rss_growth_mb": 256,
    "handles_growth": 128, "threads_growth": 16, "tcp_connections_growth": 10,
    "db_connections_growth": 10, "drain_timeout_s": 900,
    "pending_queue_growth": 100,
}
REQUIRED_ROLES = {"backend", "worker", "beat", "mysql", "redis", "provider", "chroma", "load_generator"}


def stamp(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("UTC evidence timestamps require a timezone")
    return parsed.timestamp()


def coverage(times: list[float], start: float, end: float, max_gap: float) -> bool:
    times = sorted(set(times))
    if len(times) < math.ceil((end - start) / max_gap):
        return False
    gaps = [times[0] - start, end - times[-1],
            *(right - left for left, right in zip(times, times[1:]))]
    return max(gaps) <= max_gap


def check_resources(rows: list[dict], scope: dict, load: dict,
                    queue_rows: list[dict], drain: dict,
                    limits: dict | None = None) -> dict:
    """No historical schema is inferred or upgraded into current evidence."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    errors: list[str] = []
    result = {"passed": False, "errors": errors, "processes": [], "limits": limits}
    try:
        run_id = load["run_id"]
        if (load.get("measurement_schema_version") != 2 or scope.get("schema_version") != 2
                or scope.get("run_id") != run_id or load.get("scenario") != "L06-long"):
            raise ValueError("schema/run/scenario mismatch")
        start, end = stamp(load["measurement_start_utc"]), stamp(load["measurement_end_utc"])
        if end - start < 3600:
            raise ValueError("measurement duration shorter than one hour")
        if not rows or not scope.get("expected_processes"):
            raise ValueError("missing metrics or bound process identities")
        grouped = defaultdict(list)
        for row in rows:
            if (str(row.get("schema_version")) != "2" or row.get("run_id") != run_id
                    or row.get("scenario") != "L06-long"):
                raise ValueError("mixed/legacy resource evidence")
            at = stamp(row["utc"])
            if not start <= at <= end:
                continue
            if row.get("error"):
                errors.append("resource_sampling_error:" + row["error"])
                continue
            key = (row["role"], int(row["pid"]), row["created_utc"])
            for field in ("rss_mb", "cpu_seconds", "threads", "handles", "tcp_connections"):
                number = float(row[field])
                if not math.isfinite(number) or number < 0:
                    raise ValueError("missing/invalid resource counter")
            grouped[key].append((at, row))
        expected = {(row["role"], int(row["pid"]), row["created_utc"])
                    for row in scope["expected_processes"]}
        if {key[0] for key in expected} != REQUIRED_ROLES:
            errors.append("required_service_roles_missing")
        # A restarted worker must not be combined with its predecessor to hide growth.
        if any(key[0] != "load_generator" and key not in expected for key in grouped):
            errors.append("service_process_churn_requires_review")
        for key in expected | set(grouped):
            samples = grouped.get(key, [])
            process = {"role": key[0], "pid": key[1], "created_utc": key[2], "passed": False}
            result["processes"].append(process)
            if not coverage([at for at, _ in samples], start, end, limits["max_sample_gap_s"]):
                errors.append(f"process_coverage_missing:{key[0]}:{key[1]}")
                continue
            first = [row for at, row in samples if at < start + limits["comparison_window_s"]]
            last = [row for at, row in samples if at >= end - limits["comparison_window_s"]]
            changes = {}
            for field in ("rss_mb", "handles", "threads", "tcp_connections"):
                before = median(float(row[field]) for row in first)
                after = median(float(row[field]) for row in last)
                changes[field] = {"first_median": before, "last_median": after,
                                  "increase": max(0, after - before)}
            rss = changes["rss_mb"]
            rss_ratio = rss["increase"] / rss["first_median"] if rss["first_median"] else None
            ok = (rss_ratio is not None and rss_ratio <= limits["rss_growth_ratio"] + 1e-9
                  and rss["increase"] <= limits["rss_growth_mb"]
                  and all(changes[field]["increase"] <= limits[field + "_growth"]
                          for field in ("handles", "threads", "tcp_connections")))
            process.update(
                passed=ok, comparisons=changes, rss_growth_ratio=rss_ratio,
                peak_rss_mb=max(float(row["rss_mb"]) for _, row in samples),
                peak_handles=max(float(row["handles"]) for _, row in samples),
                peak_threads=max(float(row["threads"]) for _, row in samples),
                peak_tcp_connections=max(float(row["tcp_connections"]) for _, row in samples),
                cpu_seconds_delta=max(float(row["cpu_seconds"]) for _, row in samples)
                - min(float(row["cpu_seconds"]) for _, row in samples),
            )
            if key[0] == "load_generator":
                # The generator retains latency samples for percentiles. Its
                # RSS is reported for diagnosis but growth here does not prove
                # a service leak; service identities remain fail-closed.
                process["passed"] = True
                process["growth_exempt"] = "load_generator_controller"
            elif not ok:
                errors.append(f"resource_growth_exceeded:{key[0]}:{key[1]}")
        observed_queues = []
        for row in queue_rows:
            if (str(row.get("schema_version")) != "2" or row.get("run_id") != run_id
                    or row.get("scenario") != "L06-long"):
                raise ValueError("mixed/legacy queue evidence")
            at = stamp(row["utc"])
            if not start <= at <= end:
                continue
            if row.get("error"):
                errors.append("queue_probe_error:" + row["error"])
                continue
            for name in (*PENDING_KEYS, *FAILURE_KEYS, "threads_connected", "threads_running"):
                if not str(row.get(name, "")).isdigit():
                    raise ValueError("missing/invalid queue counter")
            observed_queues.append((at, row))
            if any(int(row[key]) for key in FAILURE_KEYS):
                errors.append("failed_unknown_or_blocked_work_observed")
        queue_covered = coverage([at for at, _ in observed_queues], start, end,
                                 limits["max_sample_gap_s"])
        if not queue_covered:
            errors.append("queue_coverage_missing")
        else:
            first = [int(row["threads_connected"]) for at, row in observed_queues
                     if at < start + limits["comparison_window_s"]]
            last = [int(row["threads_connected"]) for at, row in observed_queues
                    if at >= end - limits["comparison_window_s"]]
            result["db_connections_increase"] = max(0, median(last) - median(first))
            if result["db_connections_increase"] > limits["db_connections_growth"]:
                errors.append("db_connection_growth_exceeded")
            pending_first = [sum(int(row[key]) for key in PENDING_KEYS)
                             for at, row in observed_queues
                             if at < start + limits["comparison_window_s"]]
            pending_last = [sum(int(row[key]) for key in PENDING_KEYS)
                            for at, row in observed_queues
                            if at >= end - limits["comparison_window_s"]]
            result["pending_queue_increase"] = max(0, median(pending_last) - median(pending_first))
            if result["pending_queue_increase"] > limits["pending_queue_growth"]:
                errors.append("pending_queue_growth_exceeded")
        snapshots = drain.get("snapshots", [])
        drain_ok = (
            drain.get("measurement_schema_version") == 2
            and drain.get("run_id") == run_id and drain.get("passed") is True
            and drain.get("scenario") == "L06-long"
            and 0 <= float(drain.get("elapsed_s", -1)) <= limits["drain_timeout_s"]
            and stamp(drain["began_utc"]) >= end and len(snapshots) >= 2
            and 0 <= stamp(drain["ended_utc"]) - stamp(drain["began_utc"]) <= limits["drain_timeout_s"]
            and stamp(snapshots[-1]["utc"]) <= stamp(drain["ended_utc"])
            and stamp(snapshots[-1]["utc"]) - stamp(snapshots[-2]["utc"]) >= 5
            and all(row.get("run_id") == run_id for row in snapshots[-2:])
            and all(int(row[key]) == 0 for row in snapshots[-2:]
                    for key in (*PENDING_KEYS, *FAILURE_KEYS))
        )
        if not drain_ok:
            errors.append("queue_drain_unproven")
        result.update(duration_s=end - start, queue_drain_ok=drain_ok,
                      passed=not errors and bool(result["processes"]))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        errors.append("invalid_evidence:" + str(exc))
    result["errors"] = sorted(set(errors))
    return result
