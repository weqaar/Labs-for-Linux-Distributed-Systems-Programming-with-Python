"""Functional tests for the relay Airflow blueprint.

These tests drive the public composition function ``build_relay_dag()`` the
way an Airflow adapter would use it: build the DAG, serialize the blueprint
to JSON, order its tasks by dependency and request a backfill window.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from graphlib import TopologicalSorter

import pytest

from lab_31_airflow_dags import UTC, build_relay_dag


def test_blueprint_round_trips_through_json_and_runs_from_sensor_to_metrics() -> None:
    blueprint = json.loads(json.dumps(build_relay_dag().as_airflow_blueprint()))

    tasks = blueprint["tasks"]
    graph = {task["task_id"]: set(task["upstream"]) for task in tasks}
    order = list(TopologicalSorter(graph).static_order())

    assert blueprint["dag_id"] == "relay-daily"
    assert order == [
        "wait-for-partition-snapshot",
        "discover-pending-tasks",
        "hydrate-task-context",
        "run-relay-task",
        "persist-task-status",
        "publish-run-metrics",
    ]
    worker = next(task for task in tasks if task["task_id"] == "run-relay-task")
    assert worker["resources"]["pool"] == "relay-workers"
    assert worker["retry_delay_seconds"] == 300


def test_backfill_after_a_long_outage_requests_only_the_newest_runs() -> None:
    schedule = build_relay_dag().schedule
    latest = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    runs = schedule.bounded_backfill(earliest=schedule.start_at, latest=latest)

    assert len(runs) == schedule.max_backfill_runs
    assert runs[-1] == datetime(2026, 3, 1, 2, 0, tzinfo=UTC)
    assert all(later - earlier == timedelta(days=1) for earlier, later in zip(runs, runs[1:]))


def test_backfill_with_local_time_and_unknown_task_are_rejected() -> None:
    dag = build_relay_dag()
    local_zone = timezone(timedelta(hours=2))

    with pytest.raises(ValueError, match="timezone.utc"):
        dag.schedule.bounded_backfill(
            earliest=datetime(2026, 1, 1, tzinfo=local_zone),
            latest=datetime(2026, 1, 5, tzinfo=local_zone),
        )
    with pytest.raises(KeyError, match="task-17"):
        dag.task("task-17")
