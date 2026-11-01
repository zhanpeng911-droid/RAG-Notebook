"""Offline regressions for the frozen performance contract (no services)."""
import pytest

from tools.backend_acceptance.perf_measurement import (
    new_group, record_request, summarize_group, throughput_checks,
)


@pytest.mark.parametrize("qps", [50, 100])
def test_95_percent_is_not_the_frozen_throughput_target(qps):
    checks = throughput_checks(target_qps=qps, started=int(qps * 95),
                               successes_in_window=int(qps * 95), duration=100, dropped=0)
    assert not checks["arrival_ok"]
    assert not checks["success_throughput_ok"]
    assert not checks["throughput_ok"]


@pytest.mark.parametrize("ratio,passed", [(0.98, True), (1.02, True), (1.03, False)])
def test_arrival_has_both_a_lower_and_an_upper_bound(ratio, passed):
    checks = throughput_checks(target_qps=100, started=round(10000 * ratio),
                               successes_in_window=9900, duration=100, dropped=0)
    assert checks["arrival_ok"] is passed


def test_a_dropped_request_cannot_pass_the_target_load():
    result = throughput_checks(target_qps=50, started=5000,
                               successes_in_window=4999, duration=100, dropped=1)
    assert not result["throughput_ok"]


def test_planned_in_window_but_started_late_does_not_inflate_qps():
    row = new_group()
    record_request(row, planned=19, started=21, finished=22,
                   sample_start=10, sample_end=20, status=200, error="", content_ok=True)
    assert row["started"] == 0
    assert row["late_starts"] == 1


def test_drain_completion_is_in_error_cohort_but_not_window_throughput():
    row = new_group()
    record_request(row, planned=19, started=19.5, finished=21,
                   sample_start=10, sample_end=20, status=200, error="", content_ok=True)
    assert row["started"] == row["success"] == 1
    assert row["success_in_window"] == 0
    assert row["latencies"] == [2000]
    assert row["service_latencies"] == [1500]


def test_warmup_completion_does_not_inflate_measured_successes():
    row = new_group()
    record_request(row, planned=8, started=9, finished=11,
                   sample_start=10, sample_end=20, status=200, error="", content_ok=True)
    assert row["started"] == row["success_in_window"] == 0


def test_timeout_is_retained_in_errors_and_all_result_latency():
    row = new_group()
    record_request(row, planned=10, started=10, finished=25,
                   sample_start=10, sample_end=20, status=None, error="timeout", content_ok=False)
    result = summarize_group(row, target={"steady_p95_ms": 300, "steady_p99_ms": 800},
                             error_limit=.001, min_samples=1, duration=10, tier="steady")
    assert result["timeouts"] == 1
    assert result["error_rate"] == 1
    assert result["all_results_p99_ms"] == 15000
    assert not result["passed"]


def test_boundary_results_do_not_claim_to_pass_latency_slos():
    row = new_group()
    record_request(row, planned=10, started=10, finished=11,
                   sample_start=10, sample_end=20, status=200, error="", content_ok=True)
    result = summarize_group(row, target={"peak_p95_ms": 600, "peak_p99_ms": 1600},
                             error_limit=.005, min_samples=1, duration=10, tier="boundary")
    assert result["boundary_observation"]
    assert not result["passed"]
