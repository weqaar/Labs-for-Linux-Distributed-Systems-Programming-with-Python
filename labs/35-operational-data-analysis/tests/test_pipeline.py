"""Tests for the analysis pipeline that wires loading, stats and rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lab_35_operational_data_analysis.pipeline import build_report
from lab_35_operational_data_analysis.schema import default_dataset_path

FIXED_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _fixed_clock() -> datetime:
    return FIXED_NOW


def test_build_report_against_the_bundled_fixture() -> None:
    report = build_report(default_dataset_path(), now=_fixed_clock)
    assert report.load_report.valid_count == 329
    assert report.load_report.rejected_count == 6
    assert len(report.summaries) == 2
    assert report.generated_at == FIXED_NOW
    html = report.as_html()
    assert "<html" in html
    assert "2024.05.0" in html
    assert "2024.05.1" in html


def test_build_report_is_deterministic_given_a_fixed_clock() -> None:
    first = build_report(default_dataset_path(), now=_fixed_clock)
    second = build_report(default_dataset_path(), now=_fixed_clock)
    assert first.as_html() == second.as_html()
    assert first.svg == second.svg


def test_build_report_uses_the_real_clock_by_default() -> None:
    before = datetime.now(timezone.utc)
    report = build_report(default_dataset_path())
    after = datetime.now(timezone.utc)
    assert before <= report.generated_at <= after


def test_build_report_rejects_a_file_with_no_valid_rows(tmp_path: Path) -> None:
    from lab_35_operational_data_analysis.schema import REQUIRED_COLUMNS

    path = tmp_path / "empty.csv"
    header = ",".join(REQUIRED_COLUMNS)
    bad_row = "not-a-timestamp,task-1,rel-1,eastus,1,100.0,succeeded,1"
    path.write_text(f"{header}\n{bad_row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no valid observations"):
        build_report(path, now=_fixed_clock)


def test_as_html_never_exposes_the_absolute_dataset_path() -> None:
    path = default_dataset_path()
    report = build_report(path, now=_fixed_clock)
    html = report.as_html()
    # The report names the file, but not the directories it lives under on
    # the server that rendered it.
    assert path.name in html
    assert str(path) not in html
    assert str(path.parent) not in html
