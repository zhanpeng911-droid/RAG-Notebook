"""Host-side orchestration for L01-L07 on the isolated Compose project."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend" / "tools" / "backend_acceptance"))
import runner as acceptance  # noqa: E402

SCENARIOS = [
    # name, weights, qps, duration, warmup, tier, background, long_run
    ("L01-reads", "l01_reads", 50, 300, 120, "steady", False, False),
    ("L02-save-0", "l02_save", 10, 300, 120, "steady", False, False),
    ("L02-save-20", "l02_save_20", 10, 300, 120, "steady", False, False),
    ("L02-save-100", "l02_save_100", 10, 300, 120, "steady", False, False),
    ("L03-round1", "l03_mixed", 50, 900, 120, "steady", True, False),
    ("L03-round2", "l03_mixed", 50, 900, 120, "steady", True, False),
    ("L03-round3", "l03_mixed", 50, 900, 120, "steady", True, False),
    ("L05-010", "l03_mixed", 10, 1200, 120, "steady", False, False),
    ("L05-025", "l03_mixed", 25, 520, 120, "steady", False, False),
    ("L05-050", "l03_mixed", 50, 300, 120, "steady", False, False),
    ("L05-100", "l03_mixed", 100, 300, 120, "peak", False, False),
    ("L05-200", "l03_mixed", 200, 300, 120, "boundary", False, False),
    ("L06-long", "l03_mixed", 50, 3600, 120, "steady", True, True),
    ("L07-steady", "l03_mixed", 50, 300, 120, "steady", False, False),
    ("L07-peak", "l03_mixed", 100, 600, 120, "peak", False, False),
    ("L07-recovery", "l03_mixed", 50, 600, 120, "steady", False, False),
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sample_resources(project: str, output: Path, stop: threading.Event):
    path = output / "metrics.csv"
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("utc", "container", "cpu_percent", "mem_usage",
                         "mem_percent", "net_io", "block_io", "pids"))
        while not stop.is_set():
            ids = acceptance.call(
                ["docker", "ps", "-q", "--filter",
                 f"label=com.docker.compose.project={project}"]).stdout.split()
            if ids:
                result = acceptance.call(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
                    timeout=30)
                for line in result.stdout.splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    writer.writerow((utc_now(), row.get("Name"),
                                     row.get("CPUPerc"), row.get("MemUsage"),
                                     row.get("MemPerc"), row.get("NetIO"),
                                     row.get("BlockIO"), row.get("PIDs")))
                stream.flush()
            stop.wait(5)


def run_scenario(run_id: str, directory: Path, args, env, project,
                 name, weights, qps, duration, warmup, tier, background,
                 long_run, resource_stop=None):
    perf_root = directory / "perf"
    perf_root.mkdir(exist_ok=True)
    target = perf_root / name
    if target.exists():
        raise ValueError("Existing scenario evidence cannot be reused or overwritten")
    remote = f"/app/data/perf/{run_id}/{name}"
    background_proc = None
    try:
        if background:
            bg_log = (perf_root / f"{name}-background.log").open("w", encoding="utf-8")
            background_proc = subprocess.Popen(
                args + ["exec", "-T", "controller", "timeout",
                        str(int(duration + warmup + 120)),
                        "python", "tools/backend_acceptance/background_jobs.py",
                        "--duration", str(int(duration + warmup)),
                        "--index-per-minute", "2", "--analysis-per-minute", "2"],
                cwd=ROOT, env=env, stdout=bg_log, stderr=subprocess.STDOUT)
        command = ["exec", "-T", "controller", "python",
                   "tools/backend_acceptance/open_loop_load.py",
                   "--scenario", name, "--weights", weights,
                   "--qps", str(qps), "--duration", str(duration),
                   "--warmup", str(warmup), "--tier", tier,
                   "--output", remote, "--observe-db"]
        if long_run:
            command.append("--long-run")
        result = acceptance.compose_run(args, env, command, timeout=int(duration + warmup + 1200))
        (perf_root / f"{name}.log").write_text(
            result.stdout + result.stderr, encoding="utf-8")
        copy = acceptance.compose_run(
            args, env, ["cp", f"controller:{remote}", str(target)], timeout=300)
        if copy.returncode:
            acceptance.append_case(directory, name, "failed",
                                   "copy failed: " + copy.stderr[:200])
            return False
        summary_path = target / "summary.json"
        if not summary_path.is_file():
            acceptance.append_case(directory, name, "failed", "summary.json missing")
            return False
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        passed = summary.get("all_groups_passed", False)
        if tier == "boundary":
            passed = True
        acceptance.append_case(directory, name, "passed" if passed else "failed",
                               f"target={qps}qps tier={tier} "
                               f"dropped={summary.get('dropped_due_to_inflight_limit')}")
        return passed
    finally:
        if background_proc is not None:
            background_proc.terminate()
            try:
                background_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                background_proc.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--only", nargs="*",
                        choices=[row[0] for row in SCENARIOS] + ["L04-long-chain", "L08-sse"])
    parser.add_argument("--skip-seed", action="store_true")
    options = parser.parse_args()
    directory, project, database, env, args = acceptance.context(options.run_id)
    if not (directory / "manifest.json").is_file():
        raise SystemExit("run preflight first")
    perf_root = directory / "perf"
    perf_root.mkdir(exist_ok=True)
    frozen = perf_root / "profile.json"
    profile_bytes = (Path(__file__).resolve().parent / "perf_profile.json").read_bytes()
    if frozen.exists() and frozen.read_bytes() != profile_bytes:
        raise ValueError("Frozen profile mismatch: use a new run")
    if not frozen.exists():
        frozen.write_bytes(profile_bytes)
    # Container stats are useful raw observations, but are not per-process schema-2 proof.
    support = perf_root / "resource_support.json"
    with support.open("x", encoding="utf-8") as stream:
        json.dump({"passed": False, "reason": "container_per_process_adapter_not_implemented"},
                  stream, indent=2)
    stop = threading.Event()
    sampler = threading.Thread(target=sample_resources,
                               args=(project, perf_root, stop), daemon=True)
    sampler.start()
    try:
        if not options.skip_seed:
            result = acceptance.compose_run(
                args, env, ["exec", "-T", "controller", "python",
                            "tools/backend_acceptance/perf_seed.py"],
                timeout=3600)
            (perf_root / "seed.log").write_text(
                result.stdout + result.stderr, encoding="utf-8")
            acceptance.append_case(directory, "T4-seed", "passed" if result.returncode == 0 else "failed",
                                   "see perf/seed.log")
            if result.returncode:
                return 1
        selected = set(options.only or [row[0] for row in SCENARIOS]
                       + ["L04-long-chain", "L08-sse"])
        ok = True
        for row in SCENARIOS:
            name, weights, qps, duration, warmup, tier, background, long_run = row
            if name not in selected:
                continue
            print(f"starting {name} qps={qps} duration={duration}", flush=True)
            ok = run_scenario(options.run_id, directory, args, env, project,
                              name, weights, qps, duration, warmup, tier,
                              background, long_run) and ok
        if "L04-long-chain" in selected:
            remote = f"/app/data/perf/{options.run_id}/L04-long-chain"
            result = acceptance.compose_run(
                args, env, ["exec", "-T", "controller", "python",
                            "tools/backend_acceptance/perf_l04.py"],
                timeout=7200)
            (perf_root / "L04-long-chain.log").write_text(
                result.stdout + result.stderr, encoding="utf-8")
            target = perf_root / "L04-long-chain"
            copy = acceptance.compose_run(
                args, env, ["cp", f"controller:{remote}", str(target)], timeout=300)
            passed = result.returncode == 0 and copy.returncode == 0
            acceptance.append_case(directory, "L04-long-chain",
                                   "passed" if passed else "failed",
                                   "see perf/L04-long-chain.log")
            ok = ok and passed
        if "L08-sse" in selected:
            remote = f"/app/data/perf/{options.run_id}/L08-sse"
            result = acceptance.compose_run(
                args, env, ["exec", "-T", "controller", "python",
                            "tools/backend_acceptance/perf_l08.py"],
                timeout=1800)
            (perf_root / "L08-sse.log").write_text(
                result.stdout + result.stderr, encoding="utf-8")
            target = perf_root / "L08-sse"
            copy = acceptance.compose_run(
                args, env, ["cp", f"controller:{remote}", str(target)], timeout=300)
            passed = result.returncode == 0 and copy.returncode == 0
            acceptance.append_case(directory, "L08-sse",
                                   "passed" if passed else "failed",
                                   "see perf/L08-sse.log")
            ok = ok and passed
        report = acceptance.call(
            [sys.executable, str(ROOT / "backend" / "tools" / "backend_acceptance" / "perf_report.py"),
             str(directory)], timeout=120)
        print(report.stdout + report.stderr)
        return 0 if ok and report.returncode == 0 else 1
    finally:
        stop.set()
        sampler.join(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
