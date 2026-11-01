"""Bounded HTTP mock: exercises CSV/summary wiring without any application service."""
import argparse
import asyncio
import csv
import json
import time
from datetime import datetime

import httpx
import pytest

from tools.backend_acceptance import open_loop_load as load


@pytest.mark.asyncio
async def test_csv_records_dispatch_and_finish_and_summary_uses_actual_window(tmp_path, monkeypatch):
    profile = {
        "weights": {"l02_save": {"save_0": 1}},
        "targets": {"steady_min_samples_per_endpoint": 1, "steady_error_rate": .001,
                    "groups": {"save_0": {"steady_p95_ms": 500, "steady_p99_ms": 1500}}},
    }
    (tmp_path / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (tmp_path / "accounts.json").write_text(
        json.dumps([{"user_id": "synthetic-owner", "token": "not-a-real-token"}]), encoding="utf-8")
    dispatches = []

    async def respond(request):
        dispatches.append(request.url.path)
        return httpx.Response(200, json={"data": {"id": "synthetic-note"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(load.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(load, "RUN", "nb-it-synthetic")
    args = argparse.Namespace(
        profile=str(tmp_path / "profile.json"), accounts=str(tmp_path / "accounts.json"),
        documents=str(tmp_path / "no-documents.json"), weights="l02_save", link_count=0,
        output=str(tmp_path / "result"), qps=100, duration=.03, warmup=.02,
        max_inflight=8, seed=1, timeout=1, tier="steady", long_run=False,
        scenario="synthetic-csv",
    )
    result = await load.run(args)
    with (tmp_path / "result" / "requests.csv").open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert dispatches and len(rows) == len(dispatches)
    start = datetime.fromisoformat(result["measurement_start_utc"])
    end = datetime.fromisoformat(result["measurement_end_utc"])
    actual_starts = window_successes = 0
    for row in rows:
        planned, began, finished = (datetime.fromisoformat(row[key])
                                    for key in ("planned_utc", "started_utc", "finished_utc"))
        assert planned <= began <= finished
        assert float(row["elapsed_ms"]) >= float(row["service_ms"]) >= 0
        assert row["started_in_window"] == str(start <= began < end)
        actual_starts += int(start <= began < end)
        window_successes += int(start <= began < end and finished < end)
    endpoint = result["endpoints"]["save_0"]
    assert endpoint["requests_started"] == actual_starts
    assert endpoint["successes_in_window"] == window_successes
    assert result["success_qps"] == window_successes / args.duration


def test_state_workload_cannot_fall_back_to_a_health_probe():
    scenario = load.Scenario({"weights": {"reads": {"state": 1}}}, "reads",
                             [{"user_id": "u", "token": "synthetic"}], {})
    with pytest.raises(ValueError, match="Missing seeded document"):
        scenario.request("state", 0, 0)


@pytest.mark.asyncio
async def test_dispatch_wait_never_clamps_an_early_timer(monkeypatch):
    real_sleep = asyncio.sleep
    early_wakes = 5

    async def early_timer(delay):
        nonlocal early_wakes
        if early_wakes:
            early_wakes -= 1
            await real_sleep(0)
            return
        await real_sleep(delay)

    monkeypatch.setattr(load.asyncio, "sleep", early_timer)
    planned = time.perf_counter() + .03
    began = await load.wait_for_arrival(planned)
    assert began >= planned
    assert early_wakes == 0
