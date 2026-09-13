"""Tests for SVG chart rendering and HTML report rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lab_35_operational_data_analysis.analysis import (
    ComparisonResult,
    ConfidenceInterval,
    RegressionResult,
    ReleaseSummary,
    observations_to_frame,
    summarize_all_releases,
)
from lab_35_operational_data_analysis.report import (
    current_environment,
    render_html_report,
    render_svg_report,
)
from lab_35_operational_data_analysis.schema import Observation, RejectedRow

EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _frame():
    observations = tuple(
        Observation(
            timestamp=EPOCH + timedelta(seconds=i),
            task_id=f"t{i}",
            release="rel-a" if i < 5 else "rel-b",
            region="eastus",
            queue_depth_at_submit=i % 4,
            duration_ms=float(100 + i * 3),
            outcome="failed" if i == 0 else "succeeded",
            attempt=1,
        )
        for i in range(10)
    )
    return observations_to_frame(observations)


def _summaries(frame):
    return summarize_all_releases(frame)


def test_render_svg_report_is_an_accessible_svg_document() -> None:
    frame = _frame()
    svg = render_svg_report(frame, _summaries(frame))
    assert svg.strip().startswith("<?xml") or "<svg" in svg[:200]
    assert 'role="img"' in svg
    assert "<title>" in svg
    assert "<desc>" in svg
    assert "relay task duration" in svg


def test_render_svg_report_is_deterministic() -> None:
    frame = _frame()
    summaries = _summaries(frame)
    first = render_svg_report(frame, summaries)
    second = render_svg_report(frame, summaries)
    assert first == second


def _sample_regression() -> RegressionResult:
    return RegressionResult(
        intercept_ms=100.0,
        queue_depth_coefficient_ms=-0.5,
        release_coefficients_ms={"rel-b": 12.0},
        r_squared=0.05,
        durbin_watson=2.0,
        residual_std_ms=20.0,
        queue_depth_coefficient_with_region_control_ms=2.0,
    )


def _sample_comparison(candidate_release: str = "rel-b") -> ComparisonResult:
    return ComparisonResult(
        baseline_release="rel-a",
        candidate_release=candidate_release,
        baseline_n=5,
        candidate_n=5,
        u_statistic=10.0,
        p_value=0.03,
        rank_biserial=0.4,
    )


def _sample_confidence() -> ConfidenceInterval:
    return ConfidenceInterval(
        mean_ms=150.0, lower_ms=140.0, upper_ms=160.0, confidence=0.95, sample_size=10
    )


def test_render_html_report_escapes_hostile_input() -> None:
    frame = _frame()
    svg = render_svg_report(frame, _summaries(frame))
    hostile_release = "<script>alert(1)</script>"
    summaries = (
        ReleaseSummary(
            release=hostile_release,
            count=1,
            failures=0,
            failure_rate=0.0,
            mean_ms=1.0,
            median_ms=1.0,
            p95_ms=1.0,
            p99_ms=1.0,
            sample_std_ms=0.0,
        ),
    )
    rejected = (
        RejectedRow(
            line_number=2, reason="<img src=x onerror=alert(1)>", raw={"task_id": "<b>x</b>"}
        ),
    )

    html = render_html_report(
        source_label="<path>/observations.csv",
        generated_at_label="2024-01-01T00:00:00+00:00",
        valid_count=1,
        rejected=rejected,
        duplicate_task_ids=0,
        summaries=summaries,
        confidence=_sample_confidence(),
        comparison=_sample_comparison(hostile_release),
        regression=_sample_regression(),
        svg=svg,
        environment=current_environment(),
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<path>/observations.csv" not in html
    assert "&lt;path&gt;" in html


def test_render_html_report_contains_the_numeric_summary_alongside_the_chart() -> None:
    frame = _frame()
    summaries = _summaries(frame)
    svg = render_svg_report(frame, summaries)
    html = render_html_report(
        source_label="observations.csv",
        generated_at_label="2024-01-01T00:00:00+00:00",
        valid_count=10,
        rejected=(),
        duplicate_task_ids=0,
        summaries=summaries,
        confidence=_sample_confidence(),
        comparison=_sample_comparison(),
        regression=_sample_regression(),
        svg=svg,
        environment=current_environment(),
    )
    assert "<html" in html
    assert "Per-release summary" in html
    assert "Confidence interval" in html
    assert "Release comparison" in html
    assert "Duration model" in html
    assert "Reproducibility" in html
    assert svg in html
    assert "No rows were rejected." in html


def test_render_html_report_summarizes_many_rejected_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _frame()
    summaries = _summaries(frame)
    svg = render_svg_report(frame, summaries)
    rejected = tuple(RejectedRow(line_number=i, reason=f"reason {i}", raw={}) for i in range(2, 20))
    html = render_html_report(
        source_label="observations.csv",
        generated_at_label="2024-01-01T00:00:00+00:00",
        valid_count=10,
        rejected=rejected,
        duplicate_task_ids=0,
        summaries=summaries,
        confidence=_sample_confidence(),
        comparison=_sample_comparison(),
        regression=_sample_regression(),
        svg=svg,
        environment=current_environment(),
    )
    assert "and 8 more." in html


def test_current_environment_reports_every_library_version() -> None:
    environment = current_environment()
    assert environment.numpy
    assert environment.pandas
    assert environment.matplotlib
    assert environment.scipy
    assert environment.statsmodels
