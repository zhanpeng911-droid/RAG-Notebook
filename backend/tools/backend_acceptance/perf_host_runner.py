"""Host-mode L01-L08 orchestrator with process resource sampling."""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BACKEND = TOOLS.parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tools.backend_acceptance.host_resources import load_scope, sample_resources, with_controller  # noqa: E402
SCENARIOS = [
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
    # 100/200 QPS follow a dataset reset; the first ~3 minutes warm the
    # MySQL buffer pool and API/SQL caches. Warm-up is not part of the
    # measured window and prevents a cold-start backlog from being scored as
    # steady-state capacity.
    ("L05-100", "l03_mixed", 100, 300, 300, "peak", False, False),
    ("L05-200", "l03_mixed", 200, 300, 300, "boundary", False, False),
    ("L06-long", "l03_mixed", 50, 3600, 900, "steady", True, True),
    ("L07-steady", "l03_mixed", 50, 300, 120, "steady", False, False),
    ("L07-peak", "l03_mixed", 100, 600, 0, "peak", False, False),
    # No warm-up: the first two minutes after the drop are the recovery SLO.
    ("L07-recovery", "l03_mixed", 50, 600, 0, "steady", False, False),
]
L07_PHASES = {"L07-steady", "L07-peak", "L07-recovery"}


@contextmanager
def sampled_case(evidence: Path, name: str, scope: dict):
    target = evidence / "perf" / name
    if target.exists() and any(target.iterdir()):
        raise ValueError("Existing evidence is read-only; choose a new run directory")
    target.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    sampler = threading.Thread(target=sample_resources,
                               args=(target, stop, with_controller(scope), name), daemon=True)
    sampler.start()
    try:
        yield
    finally:
        stop.set()
        sampler.join(timeout=40)
        if sampler.is_alive():
            raise RuntimeError("Resource sampler failed to stop")


def _active_db_queries() -> int | None:
    import aiomysql
    from app.config.validator import get_settings
    settings = get_settings()

    async def count():
        conn = await aiomysql.connect(
            host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT),
            user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
            db=settings.MYSQL_DATABASE)
        own_id = conn.thread_id()
        try:
            cur = await conn.cursor()
            await cur.execute("SHOW FULL PROCESSLIST")
            rows = await cur.fetchall()
            await cur.close()
        finally:
            conn.close()
        return sum(1 for row in rows
                   if row[4] not in ("Sleep", "Daemon")
                   and row[0] != own_id)

    try:
        return asyncio.run(count())
    except Exception:
        return None


def wait_db_idle(label: str, timeout: int = 180) -> bool:
    """Do not start a scenario on top of abandoned timed-out requests."""
    deadline = time.monotonic() + timeout
    quiet = 0
    while time.monotonic() < deadline:
        active = _active_db_queries()
        if active is None:
            # A probe that cannot connect (wrong cwd/config) would otherwise
            # look busy forever and hide the real cause behind a timeout.
            print(f"{label}: database probe failed; check MySQL settings/cwd", flush=True)
            return False
        quiet = quiet + 1 if active <= 2 else 0
        if quiet >= 2:
            print(f"{label}: database idle", flush=True)
            return True
        time.sleep(5)
    print(f"{label}: database still busy after {timeout}s", flush=True)
    return False


def _api_env() -> dict:
    env = os.environ.copy()
    required = ("NB_ACCEPTANCE_RUN_ID", "NB_ACCEPTANCE_API_HOST",
                "NB_ACCEPTANCE_CHROMA_DIR", "NB_PERF_DATA")
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise SystemExit("missing host acceptance env: " + ", ".join(missing))
    return env


def run_scenario(env, evidence: Path, name, weights, qps, duration, warmup,
                 tier, background, long_run):
    target = evidence / "perf" / name
    if (target / "summary.json").is_file():
        raise ValueError("Existing scenario summary cannot be reused as a fresh result")
    target.parent.mkdir(parents=True, exist_ok=True)
    bg_log = None
    background_proc = None
    if background:
        bg_log = (target.parent / f"{name}-background.log").open("w", encoding="utf-8")
        background_proc = subprocess.Popen(
            [sys.executable, str(TOOLS / "background_jobs.py"),
             "--duration", str(int(duration + warmup)),
             "--index-per-minute", "2", "--analysis-per-minute", "2"],
            cwd=BACKEND, env=env, stdout=bg_log, stderr=subprocess.STDOUT)
    command = [
        sys.executable, str(TOOLS / "open_loop_load.py"),
        "--scenario", name, "--weights", weights,
        "--qps", str(qps), "--duration", str(duration),
        "--warmup", str(warmup), "--tier", tier,
        "--output", str(target), "--observe-db",
    ]
    if long_run:
        command.append("--long-run")
    print(f"start {name} qps={qps} duration={duration}", flush=True)
    try:
        with (target / f"{name}.log").open("x", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=BACKEND, env=env,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    timeout=int(duration + warmup + 1200))
        if result.returncode != 0:
            print(f"fail {name}", flush=True)
            return False
        return True
    finally:
        if background_proc is not None:
            background_proc.terminate()
            try:
                background_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                background_proc.kill()
        if bg_log is not None:
            bg_log.close()


def reset_data(env, label: str) -> None:
    """Restore the seeded MySQL baseline without deleting the live Chroma dir."""
    print(f"reset {label}", flush=True)
    result = subprocess.run(
        [sys.executable, str(TOOLS / "perf_reset.py"), "--keep-chroma"],
        cwd=BACKEND, env=env, timeout=600, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Dataset reset failed for {label}: {result.stderr[-500:]}")
    print(f"reset {label} done", flush=True)


def run_script(env, evidence, name, script, args, timeout):
    target = evidence / "perf" / name
    if (target / "summary.json").is_file():
        raise ValueError("Existing scenario summary cannot be reused as a fresh result")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    print(f"start {name}", flush=True)
    with (target / f"{name}.log").open("x", encoding="utf-8") as log:
        result = subprocess.run(
            [sys.executable, str(TOOLS / script), *args, "--output", str(target)],
            cwd=BACKEND, env=env, timeout=timeout, stdout=log, stderr=subprocess.STDOUT)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--process-scope", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    options = parser.parse_args()
    env = _api_env()
    if options.run_id != env["NB_ACCEPTANCE_RUN_ID"]:
        parser.error("--run-id differs from NB_ACCEPTANCE_RUN_ID")
    scope = load_scope(options.process_scope, options.run_id)
    evidence = Path(options.evidence).resolve()
    if evidence.name != options.run_id:
        parser.error("Evidence directory name must match the run id")
    perf = evidence / "perf"
    perf.mkdir(parents=True, exist_ok=True)
    # Store the exact profile bytes used in this run; preserve an existing mismatch.
    profile_bytes = (TOOLS / "perf_profile.json").read_bytes()
    frozen = perf / "profile.json"
    if frozen.exists() and frozen.read_bytes() != profile_bytes:
        raise ValueError("Profile changed within this evidence run; use a new run id")
    if not frozen.exists():
        frozen.write_bytes(profile_bytes)
    selected = set(options.only or [row[0] for row in SCENARIOS]
                   + ["L04-long-chain", "L08-sse"])
    allowed = {row[0] for row in SCENARIOS} | {"L04-long-chain", "L08-sse"}
    if not selected <= allowed:
        parser.error("Unknown scenario")
    if selected & L07_PHASES and not L07_PHASES <= selected:
        parser.error("L07 must select all three phases of the continuous chain")
    ok = True
    for row in SCENARIOS:
        name, weights, qps, duration, warmup, tier, background, long_run = row
        if name in selected:
            if name in L07_PHASES:
                if name != "L07-steady":
                    continue
                if not wait_db_idle("L07-chain"):
                    raise RuntimeError("Database idle check failed before L07 chain")
                reset_data(env, "L07-chain")
                with sampled_case(evidence, "L07-chain", scope):
                    with (perf / "L07-chain" / "run.log").open("x", encoding="utf-8") as log:
                        result = subprocess.run(
                            [sys.executable, str(TOOLS / "continuous_load.py"),
                             "--output", str(perf)], cwd=BACKEND, env=env,
                            stdout=log, stderr=subprocess.STDOUT, timeout=3000)
                        ok = result.returncode == 0 and ok
                if not wait_db_idle("L07-settle"):
                    raise RuntimeError("Database failed to settle after L07")
                continue
            if not wait_db_idle(name):
                raise RuntimeError("Database idle check failed; scenario was not started")
            reset_data(env, name)
            with sampled_case(evidence, name, scope):
                ok = run_scenario(env, evidence, name, weights, qps, duration,
                                  warmup, tier, background, long_run) and ok
            if not wait_db_idle(name + "-settle"):
                raise RuntimeError("Database failed to settle; do not stack another load")
    if "L08-sse" in selected:
        if not wait_db_idle("L08-sse"):
            raise RuntimeError("Database idle check failed before L08")
        reset_data(env, "L08-sse")
        with sampled_case(evidence, "L08-sse", scope):
            ok = run_script(env, evidence, "L08-sse", "perf_l08.py",
                            ["--requests", "200", "--concurrency", "10"], 1800) and ok
    if "L04-long-chain" in selected:
        if not wait_db_idle("L04-long-chain"):
            raise RuntimeError("Database idle check failed before L04")
        reset_data(env, "L04-long-chain")
        with sampled_case(evidence, "L04-long-chain", scope):
            ok = run_script(env, evidence, "L04-long-chain", "perf_l04.py",
                            ["--m-count", "30", "--l-count", "10",
                             "--concurrency", "4"], 7200) and ok
    report = subprocess.run(
        [sys.executable, str(TOOLS / "perf_report.py"), str(evidence), "--only", *sorted(selected)],
        cwd=BACKEND, env=env, timeout=120)
    return 0 if ok and report.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
