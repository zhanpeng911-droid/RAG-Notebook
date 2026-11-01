"""Report cannot turn old or under-driven measurements into acceptance."""
import csv
import json
from datetime import datetime, timedelta, timezone
import sys

import pytest

from tools.backend_acceptance.perf_report import (
    continuity_check, recovery_check, validate_load, validate_chain,
)


def load_summary():
    return {
        "_dir": "L05-050", "scenario": "L05-050", "measurement_schema_version": 2,
        "run_id": "nb-it-synthetic", "profile_sha256": "frozen", "target_qps": 50,
        "duration_s": 100, "dropped_due_to_inflight_limit": 0,
        "all_groups_passed": True,
        "endpoints": {"list": {"requests_started": 5000, "successes_in_window": 4990,
                               "metric_scope": "planned_to_finished_actual_start_cohort"}},
    }


def test_new_bound_load_summary_meets_the_frozen_arrival_rule():
    assert validate_load(load_summary(), profile_hash="frozen", run_id="nb-it-synthetic") == []


def test_old_green_flag_is_insufficient():
    row = load_summary()
    row.pop("measurement_schema_version")
    assert validate_load(row, profile_hash="frozen", run_id="nb-it-synthetic") == [
        "legacy_mixed_or_unbound_load_evidence"]


def test_report_recomputes_throughput_instead_of_trusting_pass_flags():
    row = load_summary()
    row["endpoints"]["list"].update(requests_started=4750, successes_in_window=4750)
    assert "frozen_throughput_threshold_failed" in validate_load(
        row, profile_hash="frozen", run_id="nb-it-synthetic")


def test_profile_from_a_different_run_is_rejected():
    assert validate_load(load_summary(), profile_hash="changed", run_id="nb-it-synthetic")


def test_boundary_data_is_still_required_to_have_current_evidence_identity():
    row = load_summary()
    row.update(_dir="L05-200", scenario="L05-200", target_qps=200)
    assert validate_load(row, profile_hash="frozen", run_id="nb-it-synthetic") == []
    row["run_id"] = "another-run"
    assert validate_load(row, profile_hash="frozen", run_id="nb-it-synthetic")


def _recovery_case(tmp_path, *, slow_seconds=0):
    case = tmp_path / "L07-recovery"
    case.mkdir()
    start = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)
    groups = {"list": {}, "save_20": {}}
    fields = ["endpoint", "planned_utc", "started_utc", "finished_utc",
              "status", "content_ok", "error", "elapsed_ms", "planned_in_window"]
    with (case / "requests.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for group in groups:
            for second in range(120):
                for index in range(25):
                    at = start + timedelta(seconds=second + index / 25)
                    elapsed = 1000.0 if second < slow_seconds else 20.0
                    writer.writerow({
                        "endpoint": group, "planned_utc": at.isoformat(),
                        "started_utc": at.isoformat(),
                        "finished_utc": (at + timedelta(milliseconds=elapsed)).isoformat(),
                        "status": "200", "content_ok": "True", "error": "",
                        "elapsed_ms": elapsed, "planned_in_window": "True",
                    })
    (case / "summary.json").write_text(json.dumps({
        "run_id": "nb-it-synthetic", "measurement_schema_version": 2,
        "measurement_start_utc": start.isoformat(),
        "measurement_end_utc": (start + timedelta(seconds=600)).isoformat(),
        "warmup_s": 0, "all_groups_passed": True, "target_qps": 50,
        "endpoints": {"list": {}, "save_20": {}},
    }), encoding="utf-8")
    peak = tmp_path / "L07-peak"
    peak.mkdir()
    (peak / "summary.json").write_text(json.dumps({
        "measurement_end_utc": start.isoformat(),
    }), encoding="utf-8")
    return {
        "targets": {
            "steady_error_rate": .001,
            "groups": {
                "list": {"steady_p95_ms": 300, "steady_p99_ms": 800},
                "save_20": {"steady_p95_ms": 500, "steady_p99_ms": 1500},
            },
        }
    }


def test_recovery_first_two_minutes_are_judged_separately(tmp_path):
    profile = _recovery_case(tmp_path)
    result = recovery_check(tmp_path, profile)
    assert result["passed"] is True
    assert result["groups"]["list"]["attempts"] == 1500
    assert result["sustained_window_s"] == 60


def test_initial_transition_spike_does_not_fail_recovery(tmp_path):
    profile = _recovery_case(tmp_path, slow_seconds=10)
    assert recovery_check(tmp_path, profile)["passed"] is True


def test_recovery_must_be_steady_by_the_two_minute_deadline(tmp_path):
    profile = _recovery_case(tmp_path, slow_seconds=90)
    result = recovery_check(tmp_path, profile)
    assert result["passed"] is False
    assert "recovery_slo_failed:list" in result["errors"]


def test_l07_phases_must_be_continuous():
    def phase(name, start, end):
        return {"_dir": name, "measurement_start_utc": start, "measurement_end_utc": end,
                "phase_chain_id": "one-client", "phase_sequence_index": {
                    "L07-steady": 0, "L07-peak": 1, "L07-recovery": 2}[name]}
    base = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)
    rows = [
        phase("L07-steady", base.isoformat(),
              (base + timedelta(seconds=300)).isoformat()),
        phase("L07-peak", (base + timedelta(seconds=300)).isoformat(),
              (base + timedelta(seconds=900)).isoformat()),
        phase("L07-recovery", (base + timedelta(seconds=900)).isoformat(),
              (base + timedelta(seconds=1500)).isoformat()),
    ]
    assert continuity_check(rows)["passed"] is True
    # Even a 28-second quiet period invalidates recovery under continuous load.
    rows[2]["measurement_start_utc"] = (base + timedelta(seconds=928)).isoformat()
    assert continuity_check(rows)["passed"] is False


def test_recovery_deadline_is_anchored_at_peak_end_not_recovery_start(tmp_path):
    profile = _recovery_case(tmp_path)
    path = tmp_path / "L07-peak" / "summary.json"
    peak = json.loads(path.read_text(encoding="utf-8"))
    peak["measurement_end_utc"] = (
        datetime.fromisoformat(peak["measurement_end_utc"]) - timedelta(seconds=28)).isoformat()
    path.write_text(json.dumps(peak), encoding="utf-8")
    result = recovery_check(tmp_path, profile)
    assert not result["passed"]
    assert "recovery_does_not_start_at_peak_end" in result["errors"]


def test_report_rechecks_sse_token_population_even_if_old_flag_is_green(tmp_path):
    case = tmp_path / "L08-sse"
    case.mkdir()
    rows = [{"ok": True, "completed": True, "first_answer": .5 if i < 68 else None,
             "first_progress": .02} for i in range(200)]
    (case / "samples.json").write_text(json.dumps(rows), encoding="utf-8")
    (case / "seed_ready.json").write_text(json.dumps({
        "passed": True, "expected": 100, "indexed": 100}), encoding="utf-8")
    item = {"_dir": "L08-sse", "measurement_schema_version": 2, "run_id": "nb-it-synthetic",
            "all_groups_passed": True}
    assert "insufficient_answer_tokens_or_sse_slo_failed" in validate_chain(
        tmp_path, item, "nb-it-synthetic")


def test_targeted_report_does_not_require_services_or_claim_a_full_pass(tmp_path, monkeypatch):
    from tools.backend_acceptance import perf_report
    perf = tmp_path / "perf"
    perf.mkdir()
    (perf / "profile.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(perf_report, "load_summaries", lambda path: [{
        "_dir": "L08-sse", "scenario": "L08-sse", "all_groups_passed": True,
        "endpoints": {}}])
    monkeypatch.setattr(perf_report, "validate_load", lambda *a, **kw: [])
    monkeypatch.setattr(perf_report, "validate_chain", lambda *a, **kw: [])

    def unexpected(*a, **kw):
        pytest.fail("An L08-only report must not require L06/L07 observations")

    monkeypatch.setattr(perf_report, "resource_summary", unexpected)
    monkeypatch.setattr(perf_report, "recovery_check", unexpected)
    monkeypatch.setattr(sys, "argv", ["perf_report", str(tmp_path), "--only", "L08-sse"])
    with pytest.raises(SystemExit) as stopped:
        perf_report.main()
    assert stopped.value.code == 0
    result = json.loads((tmp_path / "performance_targeted_summary.json").read_text(encoding="utf-8"))
    assert result["selected_scope_passed"] is True
    assert result["overall_passed"] is False
    assert result["verification_scope"] == "targeted"
    assert "L06-long" in result["not_in_scope"]
    assert not (tmp_path / "performance_summary.json").exists()
