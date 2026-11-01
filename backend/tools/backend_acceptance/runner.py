"""Isolated acceptance runner. Run from the repository root; never targets business services."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "docs" / "evidence" / "backend-tests"
COMPOSE = ROOT / "docker-compose.backend-acceptance.yml"
SUITES = {
    "storage": ["tests_real/test_mysql_consistency.py", "tests_real/test_chroma_lifecycle.py"],
    "recovery": ["tests_real/test_worker_recovery.py"],
    "large-documents": ["tests_real/test_large_documents.py"],
    "load": ["tools/backend_acceptance/load_runner.py"],
}


def parse_pytest_cases(log: str) -> list[dict]:
    cases = []
    pattern = re.compile(r"^(tests_real/[\w/]+\.py::[\w\[\]\-\.]+)\s+(PASSED|FAILED|ERROR)")
    for line in log.splitlines():
        match = pattern.match(line.strip())
        if match:
            cases.append({"node": match.group(1), "result": match.group(2).lower()})
    return cases


def write_empty_artifacts(directory: Path):
    for name in ("timeline.jsonl", "requests.csv", "metrics.csv"):
        path = directory / name
        if not path.exists():
            path.write_text("", encoding="utf-8")


def source_fingerprint() -> str:
    backend = ROOT / "backend"
    files = [backend / name for name in ("Dockerfile.backend-acceptance",
             "Dockerfile.backend-acceptance.dockerignore", "pyproject.toml", "uv.lock",
             "alembic.ini", "main.py")]
    for name in ("app", "alembic", "tests_real", "tools/backend_acceptance"):
        base = backend / name
        if base.exists():
            files.extend(path for path in base.rglob("*") if path.is_file()
                         and "__pycache__" not in path.parts and path.suffix != ".pyc"
                         and not path.name.startswith(".env"))
    files.append(COMPOSE)
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def call(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 1200):
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def context(run_id: str):
    if not re.fullmatch(r"nb-it-[a-z0-9]+(?:-[a-z0-9]+)*", run_id) or len(run_id) > 40:
        raise ValueError("run-id must start nb-it-, contain lowercase letters/numbers/hyphens, max 40")
    directory = EVIDENCE / run_id
    project = "nbaccept-" + run_id
    database = "nb_" + run_id.replace("-", "_")
    env = os.environ.copy()
    env.update(NB_ACCEPTANCE_RUN_ID=run_id, NB_DATABASE=database, COMPOSE_DISABLE_ENV_FILE="1")
    args = ["docker", "compose", "--project-name", project, "--env-file",
            str(directory / "compose.env"), "-f", str(COMPOSE)]
    return directory, project, database, env, args


def append_case(directory: Path, name: str, result: str, detail: str):
    path = directory / "cases.json"
    cases = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    cases.append({"id": name, "result": result, "time_utc": now(), "detail": detail})
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")


def compose_run(args, env, command, timeout=1200):
    return call(args + command, env=env, timeout=timeout)


def build_and_start(args, env, log_path: Path):
    # Stream output to disk so an interrupted or stalled build keeps its evidence.
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.run(args + ["up", "-d", "--build"], cwd=ROOT, env=env,
                              stdout=log, stderr=subprocess.STDOUT, timeout=3600, check=False)


def verify_owned_resources(project: str, run_id: str) -> None:
    containers = call(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"])
    volumes = call(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"])
    if containers.returncode or volumes.returncode:
        raise ValueError("cannot enumerate project resources safely")
    ids = containers.stdout.split()
    names = volumes.stdout.split()
    if ids:
        inspected = call(["docker", "inspect", *ids])
        if inspected.returncode:
            raise ValueError("cannot inspect project containers")
        for container in json.loads(inspected.stdout):
            labels = container["Config"].get("Labels") or {}
            service = labels.get("com.docker.compose.service")
            if labels.get("com.docker.compose.project") != project or service not in {
                "mysql", "redis", "provider", "backend", "worker", "beat", "controller"
            }:
                raise ValueError("unexpected container identity; refusing cleanup")
            if service in {"backend", "worker", "beat", "controller"}:
                values = container["Config"].get("Env") or []
                if f"NB_ACCEPTANCE_RUN_ID={run_id}" not in values:
                    raise ValueError("container run identity mismatch; refusing cleanup")
    if names:
        inspected = call(["docker", "volume", "inspect", *names])
        if inspected.returncode:
            raise ValueError("cannot inspect project volumes")
        for volume in json.loads(inspected.stdout):
            labels = volume.get("Labels") or {}
            if (labels.get("com.docker.compose.project") != project or
                    labels.get("com.docker.compose.volume") not in {"mysql_data", "redis_data", "backend_data"}):
                raise ValueError("unexpected volume identity; refusing cleanup")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preflight", "run", "report", "cleanup"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--suite", choices=SUITES)
    options = parser.parse_args()
    try:
        directory, project, database, env, args = context(options.run_id)
        if options.action == "preflight":
            if directory.exists():
                raise ValueError("run-id already exists; choose a new ID, evidence must not be overwritten")
            directory.mkdir(parents=True)
            (directory / "compose.env").write_text("", encoding="utf-8")
            sha = call(["git", "rev-parse", "HEAD"]).stdout.strip()
            status = call(["git", "status", "--short"]).stdout
            manifest = {"run_id": options.run_id, "project": project, "database": database,
                        "sha": sha, "dirty_summary": status, "created_utc": now(),
                        "source_fingerprint_sha256": source_fingerprint(),
                        "python": sys.version.split()[0],
                        "status": "preflight_started"}
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            write_empty_artifacts(directory)
            docker = call(["docker", "info", "--format", "{{json .ServerVersion}}"], timeout=30)
            if docker.returncode:
                append_case(directory, "T0-docker", "failed", "Docker daemon unavailable")
                (directory / "preflight.log").write_text(docker.stderr, encoding="utf-8")
                return 1
            # A preexisting project could belong to someone else; never take it over.
            existing = call(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"])
            volumes = call(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"])
            if existing.returncode or volumes.returncode or existing.stdout.strip() or volumes.stdout.strip():
                append_case(directory, "T0-scope", "failed", "project already exists or cannot be checked")
                return 1
            config = compose_run(args, env, ["config", "--quiet"])
            if config.returncode:
                (directory / "preflight.log").write_text(config.stderr, encoding="utf-8")
                append_case(directory, "T0-compose-config", "failed", "invalid compose configuration")
                return 1
            # The ownership record is written before creating resources, so failures can be cleaned safely.
            manifest.update(status="resources_owned", docker_version=docker.stdout.strip())
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            result = build_and_start(args, env, directory / "preflight.log")
            if result.returncode:
                append_case(directory, "T0-start", "failed", "Compose startup failed; see preflight.log")
                return 1
            if manifest["source_fingerprint_sha256"] != source_fingerprint():
                append_case(directory, "T0-source", "failed", "Source changed during build; use a new run")
                return 1
            # Startup alone is not an isolation or migration proof: require the real suite preflight.
            check = compose_run(args, env, ["exec", "-T", "controller", "python", "-m", "pytest",
                                            "tests_real/test_preflight.py", "-q", "-s"], timeout=600)
            with (directory / "preflight.log").open("a", encoding="utf-8") as log:
                log.write("\n" + check.stdout + check.stderr)
            append_case(directory, "T0-real-preflight", "passed" if check.returncode == 0 else "failed",
                        "See preflight.log; includes real MySQL/Redis/Chroma and worker broker identity")
            manifest["status"] = "preflight_passed" if check.returncode == 0 else "preflight_failed"
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return check.returncode
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("No manifest for this run-id; run preflight first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("run_id") != options.run_id or manifest.get("project") != project:
            raise ValueError("manifest identity mismatch")
        if options.action == "run":
            if not options.suite:
                raise ValueError("run requires --suite")
            if manifest.get("status") != "preflight_passed":
                raise ValueError("real preflight did not pass")
            if manifest.get("source_fingerprint_sha256") != source_fingerprint():
                raise ValueError("workspace differs from built source; use a new run")
            if options.suite == "load":
                previous = len(list(directory.glob("load-attempt-*.log")))
                log_name = f"load-attempt-{previous + 1}.log"
                result = call([
                    sys.executable,
                    str(ROOT / "backend" / "tools" / "backend_acceptance" / "perf_runner.py"),
                    "--run-id", options.run_id,
                ], timeout=36000)
                (directory / log_name).write_text(
                    result.stdout + result.stderr, encoding="utf-8")
                append_case(directory, "load", "passed" if result.returncode == 0 else "failed",
                            log_name)
                return result.returncode
            paths = SUITES[options.suite]
            missing = [name for name in paths if not (ROOT / "backend" / name).is_file()]
            if missing:
                append_case(directory, options.suite, "not_run", "Missing: " + ", ".join(missing))
                return 1
            previous = len(list(directory.glob(f"{options.suite}-attempt-*.log")))
            attempt = previous + 1
            command = ["exec", "-T", "controller", "python"]
            command += [paths[0]] if options.suite == "load" else ["-m", "pytest", *paths, "-v", "--tb=short"]
            result = compose_run(args, env, command, timeout=7200)
            log_name = f"{options.suite}-attempt-{attempt}.log"
            log = result.stdout + result.stderr
            (directory / log_name).write_text(log, encoding="utf-8")
            for item in parse_pytest_cases(log):
                append_case(directory, item["node"], item["result"], log_name)
            outcome = "passed" if result.returncode == 0 else "failed"
            append_case(directory, options.suite, outcome, log_name)
            return result.returncode
        if options.action == "cleanup":
            if manifest.get("status") not in {"resources_owned", "preflight_passed", "preflight_failed"}:
                raise ValueError("this run has no owned resources to clean")
            if directory.resolve().parent != EVIDENCE.resolve():
                raise ValueError("evidence path escaped its parent")
            verify_owned_resources(project, options.run_id)
            result = compose_run(args, env, ["down", "--volumes", "--remove-orphans"])
            append_case(directory, "cleanup", "passed" if result.returncode == 0 else "failed", result.stderr)
            return result.returncode
        cases = json.loads((directory / "cases.json").read_text(encoding="utf-8")) if (directory / "cases.json").exists() else []
        lines = [f"# Backend acceptance: {options.run_id}", "", f"SHA: {manifest['sha']}", "",
                 "Only recorded outcomes are evidence. Missing cases are not verified.", ""]
        lines += [f"- {item['id']}: {item['result']} — {item['detail']}" for item in cases]
        (directory / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Acceptance runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
