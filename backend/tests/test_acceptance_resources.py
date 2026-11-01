"""Offline resource regressions. Synthetic counters are not benchmark evidence."""
from datetime import datetime, timedelta, timezone

import pytest

from tools.backend_acceptance.host_resources import bind_processes
from tools.backend_acceptance.queue_probe import FAILURE_KEYS, PENDING_KEYS
from tools.backend_acceptance.resource_checks import REQUIRED_ROLES, check_resources


def evidence():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run = "nb-it-synthetic"
    identities = [{"role": role, "pid": index + 1, "created_utc": start.isoformat()}
                  for index, role in enumerate(sorted(REQUIRED_ROLES))]
    scope = {"schema_version": 2, "run_id": run, "expected_processes": identities}
    load = {"measurement_schema_version": 2, "run_id": run, "scenario": "L06-long",
            "measurement_start_utc": start.isoformat(),
            "measurement_end_utc": (start + timedelta(hours=1)).isoformat()}
    rows, queues = [], []
    for second in range(0, 3601, 10):
        at = (start + timedelta(seconds=second)).isoformat()
        for identity in identities:
            rows.append({**identity, "schema_version": "2", "run_id": run,
                         "scenario": "L06-long", "utc": at, "rss_mb": "100",
                         "cpu_seconds": "1", "threads": "4", "handles": "100",
                         "tcp_connections": "2", "error": ""})
        queues.append({"utc": at, "schema_version": "2", "run_id": run,
                       "scenario": "L06-long", **{key: "0" for key in (*PENDING_KEYS, *FAILURE_KEYS)},
                       "threads_connected": "5", "threads_running": "1", "error": ""})
    drain = {"measurement_schema_version": 2, "run_id": run, "passed": True,
             "scenario": "L06-long", "ended_utc": (start + timedelta(seconds=3611)).isoformat(),
             "elapsed_s": 10, "began_utc": (start + timedelta(seconds=3601)).isoformat(),
             "snapshots": [{"utc": (start + timedelta(seconds=second)).isoformat(), "run_id": run,
                            **{key: 0 for key in (*PENDING_KEYS, *FAILURE_KEYS)}}
                           for second in (3601, 3611)]}
    return rows, scope, load, queues, drain


def test_complete_stable_bound_process_evidence_can_pass():
    result = check_resources(*evidence())
    assert result["passed"], result["errors"]
    assert len(result["processes"]) == len(REQUIRED_ROLES)


def test_chroma_server_cannot_be_omitted_from_long_run_resource_scope():
    rows, scope, load, queues, drain = evidence()
    rows = [row for row in rows if row["role"] != "chroma"]
    scope["expected_processes"] = [row for row in scope["expected_processes"]
                                   if row["role"] != "chroma"]
    result = check_resources(rows, scope, load, queues, drain)
    assert not result["passed"]
    assert "required_service_roles_missing" in result["errors"]


@pytest.mark.parametrize("field,value", [("rss_mb", "115"), ("threads", "30"),
                                        ("handles", "300"), ("tcp_connections", "20")])
def test_span_and_peak_alone_cannot_hide_resource_growth(field, value):
    data = evidence()
    end = datetime.fromisoformat(data[2]["measurement_end_utc"])
    for row in data[0]:
        if row["role"] == "backend" and datetime.fromisoformat(row["utc"]) >= end - timedelta(minutes=10):
            row[field] = value
    result = check_resources(*data)
    assert not result["passed"]
    assert any("resource_growth_exceeded:backend" in reason for reason in result["errors"])


def test_rss_must_meet_both_percentage_and_absolute_limits():
    data = evidence()
    end = datetime.fromisoformat(data[2]["measurement_end_utc"])
    for row in data[0]:
        if row["role"] == "backend":
            row["rss_mb"] = "4300" if datetime.fromisoformat(row["utc"]) >= end - timedelta(minutes=10) else "4000"
    assert not check_resources(*data)["passed"]  # 7.5%, but +300 MiB.


@pytest.mark.parametrize("defect", ["missing_role", "legacy", "wrong_run", "missing_counter",
                                   "sampling_gap", "pid_reuse", "unknown", "queue_growth",
                                   "db_connections", "drain_missing", "drain_late", "drain_not_empty"])
def test_incomplete_or_unstable_resource_evidence_fails_closed(defect):
    rows, scope, load, queues, drain = evidence()
    if defect == "missing_role":
        rows[:] = [row for row in rows if row["role"] != "worker"]
    elif defect == "legacy":
        rows[0].pop("schema_version")
    elif defect == "wrong_run":
        rows[0]["run_id"] = "nb-it-other"
    elif defect == "missing_counter":
        rows[0]["handles"] = ""
    elif defect == "sampling_gap":
        rows[:] = [row for index, row in enumerate(rows) if index > 100]
    elif defect == "pid_reuse":
        rows[-1]["created_utc"] = "2026-01-01T00:30:00+00:00"
    elif defect == "unknown":
        queues[-1]["execution_unknown"] = "1"
    elif defect == "queue_growth":
        for row in queues[-61:]:
            row["note_pending"] = "101"
    elif defect == "db_connections":
        for row in queues[-61:]:
            row["threads_connected"] = "20"
    elif defect == "drain_missing":
        drain.clear()
    elif defect == "drain_late":
        drain["elapsed_s"] = 901
    elif defect == "drain_not_empty":
        drain["snapshots"][-1]["note_pending"] = 1
    assert not check_resources(rows, scope, load, queues, drain)["passed"]


def test_bind_includes_spawned_api_children_and_excludes_unrelated_mysql():
    born = "2026-01-01T00:00:00+00:00"
    scope = {"roots": [{"role": "backend", "pid": 10, "created_utc": born}]}
    processes = [
        {"pid": 10, "parent_pid": 1, "created_utc": born, "name": "python.exe"},
        {"pid": 11, "parent_pid": 10, "created_utc": born, "name": "python.exe"},
        {"pid": 99, "parent_pid": 1, "created_utc": born, "name": "mysqld.exe"},
    ]
    selected, errors = bind_processes(scope, processes)
    assert not errors
    assert {row["pid"] for row in selected} == {10, 11}
    assert {row["role"] for row in selected} == {"backend"}


def test_reused_root_pid_does_not_adopt_a_different_service():
    scope = {"roots": [{"role": "mysql", "pid": 10, "created_utc": "2026-01-01T00:00:00+00:00"}]}
    rows = [{"pid": 10, "parent_pid": 1, "created_utc": "2026-01-02T00:00:00+00:00",
             "name": "mysqld.exe"}]
    selected, errors = bind_processes(scope, rows)
    assert selected == []
    assert errors == ["missing_or_reused_root:mysql:10"]


def test_load_generator_sample_growth_is_reported_but_not_a_service_failure():
    rows, scope, load, queues, drain = evidence()
    end = datetime.fromisoformat(load["measurement_end_utc"])
    for row in rows:
        if (row["role"] == "load_generator"
                and datetime.fromisoformat(row["utc"]) >= end - timedelta(minutes=10)):
            row["rss_mb"] = "400"
    result = check_resources(rows, scope, load, queues, drain)
    assert result["passed"], result["errors"]
    generator = next(p for p in result["processes"] if p["role"] == "load_generator")
    assert generator["growth_exempt"] == "load_generator_controller"
    assert generator["rss_growth_ratio"] > 1
