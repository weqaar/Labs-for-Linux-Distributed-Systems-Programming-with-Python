"""Tests for the relay Airflow DAG checkpoint."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from lab_26_airflow_dags import RELAY_DAG


def test_schedule_is_utc_and_catchup_enabled() -> None:
    assert RELAY_DAG.dag_id == "relay-daily"
    assert RELAY_DAG.schedule.start_at.tzinfo is timezone.utc
    assert RELAY_DAG.schedule.cron == "0 2 * * *"
    assert RELAY_DAG.schedule.catchup is True
    assert RELAY_DAG.schedule.max_active_runs == 2


def test_backfill_is_bounded() -> None:
    backfill = RELAY_DAG.schedule.bounded_backfill(
        earliest=datetime(2026, 1, 1, tzinfo=timezone.utc),
        latest=datetime(2026, 1, 20, 12, tzinfo=timezone.utc),
    )

    assert len(backfill) == 7
    assert backfill[0] == datetime(2026, 1, 14, 2, tzinfo=timezone.utc)
    assert backfill[-1] == datetime(2026, 1, 20, 2, tzinfo=timezone.utc)


def test_sensor_and_worker_resources_are_declared() -> None:
    sensor = RELAY_DAG.task("wait-for-partition-snapshot")
    worker = RELAY_DAG.task("run-relay-task")

    assert sensor.kind == "sensor"
    assert sensor.deferrable is True
    assert sensor.resources is not None
    assert sensor.resources.pool == "artifact-waits"
    assert sensor.resources.max_active_tis_per_dag == 1

    assert worker.dynamic_map_from == "discover-pending-tasks"
    assert worker.resources is not None
    assert worker.resources.pool == "relay-workers"
    assert worker.resources.max_active_tis_per_dag == 4


def test_airflow_blueprint_stays_serialisable() -> None:
    blueprint = RELAY_DAG.as_airflow_blueprint()
    tasks = blueprint["tasks"]

    assert blueprint["schedule"] == "0 2 * * *"
    assert blueprint["timezone"] == "UTC"
    assert isinstance(tasks, list)
    assert len(tasks) == 6


def test_import_has_no_parse_time_network_calls() -> None:
    code = """
import importlib
import socket

def fail(*args, **kwargs):
    raise RuntimeError("network call attempted")

socket.create_connection = fail
module = importlib.import_module("lab_26_airflow_dags.dag")
print(module.RELAY_DAG.dag_id)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
    )

    assert completed.stdout.strip() == "relay-daily"
