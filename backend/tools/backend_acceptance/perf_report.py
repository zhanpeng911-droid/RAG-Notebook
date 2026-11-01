"""Aggregate L01-L08 summaries into one machine-judged performance report."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.backend_acceptance.perf_measurement import percentile, throughput_checks  # noqa: E402
from tools.backend_acceptance.resource_checks import check_resources  # noqa: E402
from tools.backend_acceptance.chain_metrics import completion_throughput  # noqa: E402

RECOVERY_WINDOW_S = 120
RECOVERY_SUSTAINED_S = 60
CONTINUITY_MAX_GAP_S = .001  # Only timestamp rounding, never an idle recovery period.

EXPECTED = {
    "L01-reads", "L02-save-0", "L02-save-20", "L02-save-100",
    "L03-round1", "L03-round2", "L03-round3",
    "L04-long-chain", "L05-010", "L05-025", "L05-050", "L05-100", "L05-200",
    "L06-long", "L07-steady", "L07-peak", "L07-recovery", "L08-sse",
}


def load_summaries(perf_dir: Path) -> list[dict]:
    """Load only canonical scenario directories, never retry/stale attempts."""
    rows = []
    for name in sorted(EXPECTED):
        path = perf_dir / name / "summary.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_dir"] = name
        rows.append(data)
    return rows


def resource_summary(perf_dir: Path, load: dict, limits: dict | None = None) -> dict:
    case = perf_dir / "L06-long"
    try:
        with (case / "metrics.csv").open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        with (case / "queue.csv").open(encoding="utf-8") as stream:
            queue_rows = list(csv.DictReader(stream))
        scope = json.loads((case / "process_scope.json").read_text(encoding="utf-8"))
        drain = json.loads((case / "drain.json").read_text(encoding="utf-8"))
        return check_resources(rows, scope, load, queue_rows, drain, limits)
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": ["missing_or_invalid_resource_evidence:" + type(exc).__name__]}


def _epoch(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("UTC evidence timestamps require a timezone")
    return parsed.timestamp()


def recovery_check(perf_dir: Path, profile: dict) -> dict:
    """First two minutes after the peak drop must already meet steady SLOs."""
    case = perf_dir / "L07-recovery"
    errors: list[str] = []
    result = {"passed": False, "window_s": RECOVERY_WINDOW_S,
              "errors": errors, "groups": {}}
    try:
        summary = json.loads((case / "summary.json").read_text(encoding="utf-8"))
        peak = json.loads((perf_dir / "L07-peak" / "summary.json").read_text(encoding="utf-8"))
        rows = list(csv.DictReader((case / "requests.csv").open(encoding="utf-8")))
        start = _epoch(peak["measurement_end_utc"])
    except (OSError, ValueError, KeyError) as exc:
        errors.append("missing_or_invalid_recovery_evidence:" + type(exc).__name__)
        return result
    if summary.get("warmup_s") != 0:
        errors.append("recovery_window_has_warmup")
    if abs(_epoch(summary["measurement_start_utc"]) - start) > CONTINUITY_MAX_GAP_S:
        errors.append("recovery_does_not_start_at_peak_end")
    if not summary.get("all_groups_passed"):
        errors.append("recovery_full_window_failed")
    end = start + RECOVERY_WINDOW_S
    # "Recovered within two minutes" means a sustained steady minute ending at
    # the deadline; the first seconds after the peak drop may still be draining.
    steady_start = end - RECOVERY_SUSTAINED_S
    targets = profile["targets"]["groups"]
    error_limit = profile["targets"]["steady_error_rate"]
    exercised = set(summary.get("endpoints", {}))
    arrivals = successes_in_window = dropped = 0
    try:
        for row in rows:
            planned = _epoch(row["planned_utc"])
            if not row.get("started_utc"):
                dropped += int(steady_start <= planned < end)
                continue
            started = _epoch(row["started_utc"])
            if steady_start <= started < end:
                arrivals += 1
                successes_in_window += int(
                    _epoch(row["finished_utc"]) < end and row.get("status") == "200"
                    and row.get("content_ok") == "True" and not row.get("error"))
        result["throughput"] = throughput_checks(
            target_qps=50, started=arrivals, successes_in_window=successes_in_window,
            duration=RECOVERY_SUSTAINED_S, dropped=dropped)
        if not result["throughput"]["throughput_ok"]:
            errors.append("recovery_target_load_not_met")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid_recovery_counters")
    for group, target in targets.items():
        if group not in exercised:
            # L07 uses l03_mixed; save_0/save_100/analysis_accept are defined
            # in the shared profile but are not part of this workload.
            continue
        attempts = successes = rate_limited = 0
        latencies: list[float] = []
        for row in rows:
            if row.get("endpoint") != group or not row.get("started_utc"):
                continue
            try:
                started = _epoch(row["started_utc"])
            except ValueError:
                errors.append(f"invalid_recovery_timestamp:{group}")
                break
            if not steady_start <= started < end:
                continue
            attempts += 1
            if row.get("status") == "429":
                rate_limited += 1
            if (row.get("status") == "200" and row.get("content_ok") == "True"
                    and not row.get("error")):
                successes += 1
                latencies.append(float(row["elapsed_ms"]))
        failures = attempts - successes
        error_rate = failures / attempts if attempts else 1.0
        p95, p99 = percentile(latencies, .95), percentile(latencies, .99)
        group_ok = bool(
            attempts >= 50 and p95 is not None and p99 is not None
            and p95 <= target["steady_p95_ms"] and p99 <= target["steady_p99_ms"]
            and error_rate <= error_limit and rate_limited == 0
        )
        result["groups"][group] = {
            "attempts": attempts, "successes": successes,
            "error_rate": error_rate, "p95_ms": p95, "p99_ms": p99,
            "slo_p95_ms": target["steady_p95_ms"],
            "slo_p99_ms": target["steady_p99_ms"],
            "passed": group_ok,
        }
        if not group_ok:
            errors.append(f"recovery_slo_failed:{group}")
    result["passed"] = not errors and bool(result["groups"])
    result["deadline_start_utc"] = peak.get("measurement_end_utc")
    result["sustained_window_s"] = RECOVERY_SUSTAINED_S
    result["window_start_utc"] = (datetime.fromtimestamp(
        steady_start, timezone.utc).isoformat() if result["groups"] else None)
    result["window_end_utc"] = (datetime.fromtimestamp(end, timezone.utc).isoformat()
                               if result["groups"] else None)
    return result


def continuity_check(scenarios: list[dict]) -> dict:
    """Steady -> peak -> recovery must follow each other without an idle gap."""
    by_name = {item["_dir"]: item for item in scenarios}
    names = ("L07-steady", "L07-peak", "L07-recovery")
    errors: list[str] = []
    gaps: dict[str, float] = {}
    if not all(name in by_name for name in names):
        return {"passed": False, "gap_s": gaps,
                "errors": ["l07_phase_missing"], "max_gap_s": CONTINUITY_MAX_GAP_S}
    chain_ids = {by_name[name].get("phase_chain_id") for name in names}
    if len(chain_ids) != 1 or None in chain_ids or "" in chain_ids:
        errors.append("missing_or_mixed_continuous_chain_identity")
    if [by_name[name].get("phase_sequence_index") for name in names] != [0, 1, 2]:
        errors.append("invalid_phase_sequence")
    if any(by_name[name].get("warmup_s", 0) != 0 for name in names[1:]):
        errors.append("intermediate_phase_has_warmup")
    for left, right in zip(names, names[1:]):
        try:
            # A phase's warm-up is still load: continuity is measured from the
            # previous window end to the next phase's arrival start.
            next_arrival_start = (
                _epoch(by_name[right]["measurement_start_utc"])
                - float(by_name[right].get("warmup_s") or 0)
            )
            gap = next_arrival_start - _epoch(by_name[left]["measurement_end_utc"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"invalid_phase_timestamps:{left}->{right}")
            continue
        gaps[f"{left}->{right}"] = gap
        if abs(gap) > CONTINUITY_MAX_GAP_S:
            errors.append(f"phase_gap_exceeded:{left}->{right}")
    return {"passed": not errors, "gap_s": gaps, "errors": errors,
            "max_gap_s": CONTINUITY_MAX_GAP_S}


def validate_load(item: dict, *, profile_hash: str, run_id: str) -> list[str]:
    errors = []
    expected_qps = {
        "L01-reads": 50, "L02-save-0": 10, "L02-save-20": 10, "L02-save-100": 10,
        "L03-round1": 50, "L03-round2": 50, "L03-round3": 50,
        "L05-010": 10, "L05-025": 25, "L05-050": 50, "L05-100": 100, "L05-200": 200,
        "L06-long": 50, "L07-steady": 50, "L07-peak": 100, "L07-recovery": 50,
    }
    if item["_dir"] not in expected_qps:
        return errors  # L04/L08 have separate chain/SSE metric contracts.
    if (item.get("measurement_schema_version") != 2 or item.get("run_id") != run_id
            or item.get("profile_sha256") != profile_hash
            or item.get("scenario") != item["_dir"]):
        return ["legacy_mixed_or_unbound_load_evidence"]
    if item.get("target_qps") != expected_qps[item["_dir"]]:
        return ["target_qps_differs_from_frozen_scenario"]
    endpoints = item.get("endpoints", {})
    if not endpoints or any(row.get("metric_scope") != "planned_to_finished_actual_start_cohort"
                            for row in endpoints.values()):
        return ["missing_actual_start_cohort"]
    try:
        checks = throughput_checks(
            target_qps=item["target_qps"],
            started=sum(row["requests_started"] for row in endpoints.values()),
            successes_in_window=sum(row["successes_in_window"] for row in endpoints.values()),
            duration=item["duration_s"], dropped=item["dropped_due_to_inflight_limit"])
        # Boundary is an observation, never a mandatory capacity SLO pass.
        if item["_dir"] != "L05-200" and not checks["throughput_ok"]:
            errors.append("frozen_throughput_threshold_failed")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid_load_counters")
    return errors


def validate_chain(perf_dir: Path, item: dict, run_id: str) -> list[str]:
    name = item["_dir"]
    if name not in ("L04-long-chain", "L08-sse"):
        return []
    errors = []
    try:
        if item.get("run_id") != run_id or item.get("measurement_schema_version") != 2:
            errors.append("unbound_chain_evidence")
        rows = json.loads((perf_dir / name / "samples.json").read_text(encoding="utf-8"))
        if completion_throughput(rows)["success_qps"] is None:
            errors.append("chain_wall_clock_throughput_unverified")
        if name == "L08-sse":
            from tools.backend_acceptance.perf_l08 import summarize
            if not summarize(rows)["all_groups_passed"]:
                errors.append("insufficient_answer_tokens_or_sse_slo_failed")
            ready = json.loads((perf_dir / name / "seed_ready.json").read_text(encoding="utf-8"))
            if (not ready.get("passed") or ready.get("expected", 0) < 100
                    or ready.get("indexed") != ready.get("expected")):
                errors.append("sse_seed_readiness_unproven")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        errors.append("invalid_chain_evidence:" + type(exc).__name__)
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--only", nargs="+", choices=sorted(EXPECTED),
                        help="Targeted verification; never reported as a full 18-case pass")
    args = parser.parse_args()
    selected = set(args.only or EXPECTED)
    full_scope = selected == EXPECTED
    l07 = {"L07-steady", "L07-peak", "L07-recovery"}
    if selected & l07 and not l07 <= selected:
        parser.error("Select all three continuous L07 phases")
    perf_dir = args.evidence / "perf"
    scenarios = [row for row in load_summaries(perf_dir) if row["_dir"] in selected]
    found = {item["_dir"] for item in scenarios}
    missing = sorted(selected - found)
    overall = bool(scenarios) and not missing
    for item in scenarios:
        if item.get("scenario") == "L05-200":
            continue
        if not item.get("all_groups_passed", False):
            overall = False
    profile_path = perf_dir / "profile.json"
    if not profile_path.is_file():
        raise SystemExit("Frozen profile missing: preserve legacy evidence and use a fresh run")
    profile_bytes = profile_path.read_bytes()
    profile = json.loads(profile_bytes)
    profile_hash = hashlib.sha256(profile_bytes).hexdigest()
    evidence_errors = {}
    for item in scenarios:
        errors = validate_load(item, profile_hash=profile_hash, run_id=args.evidence.name)
        errors += validate_chain(perf_dir, item, args.evidence.name)
        if errors:
            evidence_errors[item["_dir"]] = errors
            overall = False
    long_load = next((item for item in scenarios if item["_dir"] == "L06-long"), {})
    resources = (resource_summary(perf_dir, long_load, profile.get("resource_guardrails"))
                 if "L06-long" in selected else {"passed": None, "status": "not_in_scope"})
    recovery = recovery_check(perf_dir, profile) if l07 <= selected else {
        "passed": None, "status": "not_in_scope", "errors": [], "groups": {},
        "window_s": RECOVERY_WINDOW_S}
    continuity = continuity_check(scenarios) if l07 <= selected else {
        "passed": None, "status": "not_in_scope", "gap_s": {},
        "max_gap_s": CONTINUITY_MAX_GAP_S}
    if "L06-long" in selected:
        overall = overall and resources["passed"]
    if l07 <= selected:
        overall = overall and recovery["passed"] and continuity["passed"]
    selected_passed = overall
    machine = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "profile": "enterprise-v1",
        "overall_passed": selected_passed and full_scope,
        "selected_scope_passed": selected_passed,
        "verification_scope": "full" if full_scope else "targeted",
        "not_in_scope": sorted(EXPECTED - selected),
        "missing_scenarios": missing,
        "measurement_schema_version": 2,
        "profile_sha256": profile_hash,
        "evidence_errors": evidence_errors,
        "resource_checks": resources,
        "recovery_check": recovery,
        "continuity_check": continuity,
        "scenarios": scenarios,
        "caveat": (
            "Deterministic Ollama-protocol provider; results prove platform "
            "capacity for this profile, not real-model quality or arbitrary "
            "enterprise scale."
        ),
    }
    machine_name = "performance_summary.json" if full_scope else "performance_targeted_summary.json"
    report_name = "PERFORMANCE_REPORT.md" if full_scope else "PERFORMANCE_TARGETED_REPORT.md"
    (args.evidence / machine_name).write_text(
        json.dumps(machine, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# 后端性能验收报告（enterprise-v1）", "",
        f"- run_id: `{args.evidence.name}`",
        f"- 生成时间: {machine['generated_utc']}",
        f"- {'全量' if full_scope else '定向范围'}判定: **{'通过' if selected_passed else '未通过'}**",
        "- 本次范围: " + ", ".join(sorted(selected)),
        "- 未纳入项没有被标记通过；定向结果不等于当前版本全 18 场景重跑。",
        "- 模型条件: 确定性 Ollama 协议替身；不代表真实模型质量或任意企业规模。",
        "",
        "## 场景与端点",
        "",
        "| 场景 | 档位 | 目标 QPS | 端点 | 样本 | 成功 QPS | 错误率 | P95 ms | P99 ms | 门槛 P95/P99 | 判定 |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in scenarios:
        for group, row in item.get("endpoints", {}).items():
            lines.append(
                f"| {item['_dir']} | {item.get('tier')} | {item.get('target_qps')} | "
                f"{group} | {row.get('samples')} | {row.get('success_qps')} | "
                f"{row.get('error_rate')} | {row.get('p95_ms')} | {row.get('p99_ms')} | "
                f"{row.get('slo_p95_ms')}/{row.get('slo_p99_ms')} | "
                f"{'边界观测' if item['_dir'] == 'L05-200' else ('通过' if row.get('passed') and item['_dir'] not in evidence_errors else '未通过')} |")
    lines += ["", "## 资源检查", "", f"- {json.dumps(resources, ensure_ascii=False)}"]
    if l07 <= selected:
        lines += ["", "## L07 连续性与回落", "",
              f"- 阶段间隔（上限 {continuity['max_gap_s']}s）: "
              f"{json.dumps(continuity['gap_s'], ensure_ascii=False)}",
              f"- 连续性判定: {'通过' if continuity['passed'] else '未通过'}",
              f"- 回落后最晚 {recovery['window_s']}s 内、持续 "
              f"{recovery.get('sustained_window_s', RECOVERY_SUSTAINED_S)}s 稳态判定: "
              f"{'通过' if recovery['passed'] else '未通过'}"]
    for group, row in recovery.get("groups", {}).items():
        lines.append(
            f"  - {group}: attempts={row['attempts']} p95={row['p95_ms']} "
            f"p99={row['p99_ms']} error_rate={row['error_rate']} "
            f"({'通过' if row['passed'] else '未通过'})")
    if recovery["errors"]:
        lines += ["", "回落检查错误: " + ", ".join(recovery["errors"])]
    if evidence_errors:
        lines += ["", "## 无法用于本轮验收的证据", "",
                  json.dumps(evidence_errors, indent=2, ensure_ascii=False)]
    if missing:
        lines += ["", "## 未执行/缺失场景", ""] + [f"- {name}" for name in missing]
    lines += [
        "", "## 判定规则", "",
        "- 实际开始 QPS 必须在目标 ±2% 内；窗口内成功 QPS ≥目标 98%；正式窗口 dropped=0。",
        "- P95/P99 计入调度等待；排空期成功另列，不增加正式窗口吞吐。",
        "- 稳态错误率 ≤0.1%，峰值 ≤0.5%；正常场景 429 计失败。",
        "- L06 按已绑定进程逐项检查覆盖、RSS/句柄/线程/连接增长和队列排空；仅有峰值或总行数不算通过。",
        "- 每端点样本门槛：正式窗口 ≥1000，1 小时长稳 ≥10000。",
        "- 200 QPS 档是容量边界探测，不预设必须通过；其余场景必须通过。",
        "- 任何目标未达标均按“性能验收未通过”处理，需定位、修复、复测。",
    ]
    (args.evidence / report_name).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"overall_passed": machine["overall_passed"],
                      "selected_scope_passed": selected_passed, "verification_scope": machine["verification_scope"],
                      "missing": missing,
                      "scenarios": len(scenarios)}, indent=2))
    raise SystemExit(0 if overall else 1)


if __name__ == "__main__":
    main()
