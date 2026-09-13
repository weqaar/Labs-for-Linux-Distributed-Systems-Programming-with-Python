"""Tests for schema validation and CSV loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_35_operational_data_analysis.schema import (
    REQUIRED_COLUMNS,
    default_dataset_path,
    load_observations,
)

VALID_HEADER = ",".join(REQUIRED_COLUMNS)
VALID_ROW = "2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,150.0,succeeded,1"


def write_csv(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "observations.csv"
    path.write_text("\n".join([VALID_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def test_bundled_fixture_has_the_documented_counts() -> None:
    report = load_observations(default_dataset_path())
    assert report.valid_count == 329
    assert report.rejected_count == 6
    assert report.duplicate_task_ids == 9
    assert report.source == default_dataset_path()


def test_a_fully_valid_row_loads(tmp_path: Path) -> None:
    csv_path = write_csv(tmp_path, VALID_ROW)
    report = load_observations(csv_path)
    assert report.valid_count == 1
    assert report.rejected_count == 0
    observation = report.observations[0]
    assert observation.task_id == "task-1"
    assert observation.release == "2024.05.0"
    assert observation.region == "eastus"
    assert observation.queue_depth_at_submit == 3
    assert observation.duration_ms == pytest.approx(150.0)
    assert observation.outcome == "succeeded"
    assert observation.attempt == 1
    assert observation.failed is False


@pytest.mark.parametrize(
    ("row", "reason_fragment"),
    [
        ("2024-05-14T09:00:00,task-1,2024.05.0,eastus,3,150.0,succeeded,1", "must be UTC"),
        ("2024-05-14T09:00:00Z,,2024.05.0,eastus,3,150.0,succeeded,1", "missing required field"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,-1,150.0,succeeded,1", "cannot be negative"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,three,150.0,succeeded,1", "not an integer"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,not-a-number,succeeded,1", "not a number"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,0,succeeded,1", "positive"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,150.0,cancelled,1", "is not one of"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,150.0,succeeded,0", "attempt must be"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,nan,succeeded,1", "finite"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,inf,succeeded,1", "finite"),
        ("2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,-inf,succeeded,1", "finite"),
        (
            "2024-05-14T09:00:00Z,task<1>,2024.05.0,eastus,3,150.0,succeeded,1",
            "characters outside",
        ),
        (
            "2024-05-14T09:00:00Z,task-1,2024.05.0<x>,eastus,3,150.0,succeeded,1",
            "characters outside",
        ),
        (
            "2024-05-14T09:00:00Z,task-1,2024.05.0,east us,3,150.0,succeeded,1",
            "characters outside",
        ),
        (
            f"2024-05-14T09:00:00Z,{'t' * 65},2024.05.0,eastus,3,150.0,succeeded,1",
            "longer than",
        ),
        (
            f"2024-05-14T09:00:00Z,task-1,{'2' * 33},eastus,3,150.0,succeeded,1",
            "longer than",
        ),
        (
            f"2024-05-14T09:00:00Z,task-1,2024.05.0,{'e' * 33},3,150.0,succeeded,1",
            "longer than",
        ),
    ],
)
def test_each_schema_violation_is_rejected_with_a_reason(
    tmp_path: Path, row: str, reason_fragment: str
) -> None:
    csv_path = write_csv(tmp_path, row)
    report = load_observations(csv_path)
    assert report.valid_count == 0
    assert report.rejected_count == 1
    assert reason_fragment in report.rejected[0].reason
    assert report.rejected[0].line_number == 2


def test_invalid_rows_are_never_silently_dropped(tmp_path: Path) -> None:
    good = VALID_ROW
    bad = "2024-05-14T09:01:00Z,task-2,2024.05.0,eastus,-5,150.0,succeeded,1"
    csv_path = write_csv(tmp_path, good, bad)
    report = load_observations(csv_path)
    assert report.valid_count == 1
    assert report.rejected_count == 1
    assert report.rejected[0].raw["task_id"] == "task-2"


def test_a_repeated_task_id_is_counted_but_not_rejected(tmp_path: Path) -> None:
    first = "2024-05-14T09:00:00Z,task-1,2024.05.0,eastus,3,150.0,failed,1"
    retry = "2024-05-14T09:00:05Z,task-1,2024.05.0,eastus,3,120.0,succeeded,2"
    csv_path = write_csv(tmp_path, first, retry)
    report = load_observations(csv_path)
    assert report.valid_count == 2
    assert report.rejected_count == 0
    assert report.duplicate_task_ids == 1


def test_missing_columns_raise_immediately(tmp_path: Path) -> None:
    path = tmp_path / "observations.csv"
    path.write_text("timestamp,task_id\n2024-05-14T09:00:00Z,task-1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="documented columns"):
        load_observations(path)


def test_an_undocumented_extra_column_raises_immediately(tmp_path: Path) -> None:
    path = tmp_path / "observations.csv"
    header = VALID_HEADER + ",client_ip"
    row = VALID_ROW + ",203.0.113.7"
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="documented columns"):
        load_observations(path)
