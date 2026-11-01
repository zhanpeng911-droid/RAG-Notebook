"""Offline regressions for SSE coverage and concurrent chain measurements."""
from datetime import datetime, timedelta, timezone
import argparse
import csv
import json

import httpx
import pytest

from tools.backend_acceptance.chain_metrics import completion_throughput
from tools.backend_acceptance.continuous_load import phase_windows
from tools.backend_acceptance import continuous_load
from tools.backend_acceptance.perf_l08 import summarize, token_text, wait_for_seed_vectors


def sample(*, token=True, offset=0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset)
    return {"ok": token, "completed": True, "error": None if token else "completed_without_answer_token",
            "first_answer": .5 if token else None, "first_progress": .02,
            "started_utc": start.isoformat(),
            "finished_utc": (start + timedelta(seconds=2)).isoformat()}


def test_200_terminals_with_only_68_tokens_do_not_pass():
    result = summarize([sample(token=i < 68) for i in range(200)])
    assert not result["all_groups_passed"]
    assert result["missing_answer_tokens"] == 132
    assert result["endpoints"]["stream"]["samples"] == 68
    assert not result["endpoints"]["stream"]["sample_sufficient"]
    assert result["terminal_rate"] == 1


def test_200_eligible_answer_streams_have_a_complete_latency_population():
    result = summarize([sample() for _ in range(200)])
    assert result["all_groups_passed"]
    assert result["answer_token_coverage"] == 1


def test_overlapping_requests_use_wall_time_not_summed_request_time():
    result = completion_throughput([sample(), sample()])
    assert result["success_qps"] == 1  # 2 completions / 2 seconds, not / 4.


def test_old_samples_without_start_times_remain_unverified():
    assert completion_throughput([{"ok": True, "e2e_seconds": 2}])["success_qps"] is None


@pytest.mark.parametrize("event", [{"type": "answer_token"}, {"type": "answer_token", "delta": " "},
                                  {"type": "generating_answer", "delta": "text"}])
def test_event_name_without_answer_text_cannot_supply_ttft(event):
    assert not token_text(event)


@pytest.mark.asyncio
async def test_seed_readiness_checks_every_identity_not_a_fixed_sleep():
    seeds = [{"user_id": "u", "note_id": "n"}]
    result = await wait_for_seed_vectors(seeds, timeout=1, probe=lambda: {
        "n": {"user_id": "u", "note_id": "n", "doc_type": "note"}})
    assert result == {"passed": True, "expected": 1, "indexed": 1}


@pytest.mark.asyncio
async def test_foreign_vector_cannot_satisfy_seed_readiness():
    with pytest.raises(TimeoutError, match="not all indexed"):
        await wait_for_seed_vectors([{"user_id": "u", "note_id": "n"}], timeout=.01,
                                   probe=lambda: {"n": {"user_id": "someone-else",
                                                        "note_id": "n", "doc_type": "note"}})


def test_one_clock_phase_plan_has_no_intermediate_warmup_or_idle_period():
    windows = phase_windows(100, 120)
    assert windows[1]["origin"] == windows[0]["end"]
    assert windows[2]["origin"] == windows[1]["end"]
    assert windows[1]["warmup"] == windows[2]["warmup"] == 0
    assert windows[2]["start"] == windows[1]["end"]


@pytest.mark.asyncio
async def test_continuous_chain_uses_one_client_and_preserves_adjacent_windows(tmp_path, monkeypatch):
    profile = {"accounts": 1, "weights": {"l03_mixed": {"state": 1}}, "targets": {
        "steady_min_samples_per_endpoint": 1, "steady_error_rate": .001, "peak_error_rate": .005,
        "groups": {"state": {"steady_p95_ms": 300, "steady_p99_ms": 800,
                              "peak_p95_ms": 600, "peak_p99_ms": 1600}}}}
    (tmp_path / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (tmp_path / "accounts.json").write_text(json.dumps([
        {"user_id": "synthetic-owner", "token": "synthetic"}]), encoding="utf-8")
    (tmp_path / "documents.json").write_text(json.dumps([
        {"account": "synthetic-owner", "document_id": "synthetic-document"}]), encoding="utf-8")
    monkeypatch.setenv("NB_ACCEPTANCE_RUN_ID", "nb-it-synthetic")
    monkeypatch.setenv("NB_PERF_DATA", str(tmp_path))
    created = []
    real_client = httpx.AsyncClient

    def client_factory(**kw):
        created.append(True)
        return real_client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": {"status": "indexed"}})), **kw)

    async def observer(output, stop):
        await stop.wait()

    async def drain(output, timeout):
        return {"passed": True}

    monkeypatch.setattr(continuous_load.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(continuous_load, "observe", observer)
    monkeypatch.setattr(continuous_load, "wait_for_drain", drain)
    args = argparse.Namespace(profile=str(tmp_path / "profile.json"), output=str(tmp_path / "perf"),
                              max_inflight=8, timeout=1, seed=1, warmup=.02)
    phases = (("L07-steady", 50, .04, "steady"), ("L07-peak", 100, .04, "peak"),
              ("L07-recovery", 50, .04, "steady"))
    summaries = await continuous_load.run_chain(args, phases=phases)
    assert len(created) == 1
    assert len({row["phase_chain_id"] for row in summaries}) == 1
    for left, right in zip(summaries, summaries[1:]):
        assert left["measurement_end_utc"] == right["measurement_start_utc"]
    for row in summaries:
        with (tmp_path / "perf" / row["scenario"] / "requests.csv").open(encoding="utf-8") as stream:
            assert list(csv.DictReader(stream))
