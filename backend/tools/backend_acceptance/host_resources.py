"""Windows resource evidence bound to operator-selected PID/creation identities.

Capture only after isolated services have started. No command lines or secrets
are persisted, and process names never select services from the whole host.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

ROLES = {"backend", "worker", "beat", "mysql", "redis", "provider", "chroma", "load_generator"}
FIELDS = (
    "schema_version", "run_id", "scenario", "utc", "role", "pid", "created_utc",
    "root_pid", "rss_mb", "cpu_seconds", "threads", "handles", "tcp_connections",
    "disk_free_gb", "error",
)


def process_snapshot() -> list[dict]:
    # Get-CimInstance supplies children of spawned uvicorn/celery processes too.
    script = """
    $ErrorActionPreference = 'Stop'
    $tcpCounts = @{}
    try {
        Get-NetTCPConnection -ErrorAction Stop | ForEach-Object {
            $key = [string]$_.OwningProcess
            $tcpCounts[$key] = 1 + $tcpCounts[$key]
        }
        $tcpAvailable = $true
    } catch { $tcpAvailable = $false }
    $rows = @(Get-CimInstance Win32_Process | Where-Object { $_.CreationDate } | ForEach-Object {
        $procId = [int]$_.ProcessId
        $tcpValue = $null
        if ($tcpAvailable) { $tcpValue = [int]$tcpCounts[[string]$procId] }
        [PSCustomObject]@{
            pid = $procId; parent_pid = [int]$_.ParentProcessId; name = $_.Name
            created_utc = $_.CreationDate.ToUniversalTime().ToString('o')
            rss_mb = [double]$_.WorkingSetSize / 1MB
            cpu_seconds = ([double]$_.KernelModeTime + [double]$_.UserModeTime) / 10000000
            threads = [int]$_.ThreadCount; handles = [int]$_.HandleCount
            tcp_connections = $tcpValue
        }
    })
    ConvertTo-Json -InputObject $rows -Compress
    """
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=30)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Resource process snapshot unavailable")
    rows = json.loads(result.stdout)
    return rows if isinstance(rows, list) else [rows]


def bind_processes(scope: dict, processes: list[dict]) -> tuple[list[dict], list[str]]:
    """Pure identity/tree join; reused PIDs and unrelated processes are excluded."""
    by_pid = {int(row["pid"]): row for row in processes}
    bound, errors = {}, []
    for root in scope["roots"]:
        pid = int(root["pid"])
        row = by_pid.get(pid)
        if row is None or row["created_utc"] != root["created_utc"]:
            errors.append(f"missing_or_reused_root:{root['role']}:{pid}")
            continue
        bound[pid] = {**row, "role": root["role"], "root_pid": pid}
    changed = True
    while changed:
        changed = False
        for pid, row in by_pid.items():
            parent = bound.get(int(row["parent_pid"]))
            if pid in bound or parent is None:
                continue
            if parent["role"] == "load_generator" and not row["name"].lower().startswith("python"):
                continue  # The sampler's temporary PowerShell helpers aren't the load generator.
            if datetime.fromisoformat(row["created_utc"]) < datetime.fromisoformat(parent["created_utc"]):
                continue  # Parent PID reuse must not adopt an older unrelated process.
            bound[pid] = {**row, "role": parent["role"], "root_pid": parent["root_pid"]}
            changed = True
    return list(bound.values()), errors


def load_scope(path: Path, run_id: str) -> dict:
    scope = json.loads(path.read_text(encoding="utf-8"))
    roots = scope.get("roots", [])
    if scope.get("schema_version") != 2 or scope.get("run_id") != run_id:
        raise ValueError("Process scope must belong to the current schema/run")
    if not roots or len({row["pid"] for row in roots}) != len(roots):
        raise ValueError("Process roots must be nonempty and unique")
    if {row["role"] for row in roots} != ROLES - {"load_generator"}:
        raise ValueError("Explicit backend/worker/beat/mysql/redis/provider/chroma roots required")
    return scope


def sample_resources(output: Path, stop: threading.Event, scope: dict, scenario: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "process_scope.json").write_text(json.dumps(scope, indent=2), encoding="utf-8")
    with (output / "metrics.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        while not stop.is_set():
            base = {"schema_version": 2, "run_id": scope["run_id"], "scenario": scenario,
                    "utc": datetime.now(timezone.utc).isoformat()}
            try:
                base["disk_free_gb"] = shutil.disk_usage(output.resolve().anchor).free / 1024**3
                processes, errors = bind_processes(scope, process_snapshot())
                base["utc"] = datetime.now(timezone.utc).isoformat()
                for proc in processes:
                    writer.writerow({**base, **proc})
                for error in errors:
                    writer.writerow({**base, "error": error})
            except Exception as exc:
                writer.writerow({**base, "error": type(exc).__name__})
            stream.flush()
            stop.wait(5)


def with_controller(scope: dict) -> dict:
    snapshot = process_snapshot()
    controller = next(row for row in snapshot if row["pid"] == os.getpid())
    roots = [*scope["roots"], {"role": "load_generator", "pid": controller["pid"],
                              "created_utc": controller["created_utc"]}]
    bound, errors = bind_processes({"roots": roots}, snapshot)
    if errors:
        raise ValueError("Process scope has expired; recapture after services start")
    # Capture all current service descendants, not just a low-memory wrapper PID.
    return {**scope, "roots": roots, "expected_processes": [
        {"role": row["role"], "pid": row["pid"], "created_utc": row["created_utc"]}
        for row in bound
    ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--process", action="append", required=True, help="role=PID; repeat per root")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_id.startswith("nb-it-"):
        parser.error("Use an isolated acceptance run id")
    processes = {row["pid"]: row for row in process_snapshot()}
    roots = []
    for item in args.process:
        role, pid_text = item.split("=", 1)
        pid = int(pid_text)
        if role not in ROLES - {"load_generator"} or pid not in processes:
            parser.error("Unknown role or missing process")
        roots.append({"role": role, "pid": pid, "created_utc": processes[pid]["created_utc"]})
    if {row["role"] for row in roots} != ROLES - {"load_generator"}:
        parser.error("Supply all seven service roles, including the Chroma server")
    if len({row["pid"] for row in roots}) != len(roots):
        parser.error("Do not assign the same root to multiple roles")
    # Reject overlapping roots, which would otherwise make descendant attribution ambiguous.
    for root in roots:
        descendants, _ = bind_processes({"roots": [root]}, list(processes.values()))
        if any(row["pid"] != root["pid"] and row["pid"] in {r["pid"] for r in roots}
               for row in descendants):
            parser.error("Service process roots must not overlap")
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": 2, "run_id": args.run_id, "roots": roots}, stream, indent=2)


if __name__ == "__main__":
    main()
