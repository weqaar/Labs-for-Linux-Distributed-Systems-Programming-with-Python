"""Orchestration: load a CSV, run the analysis, render the report.

Kept separate from the HTTP service so the entire pipeline can be tested
without a socket, and so the same functions can be reused from a script or a
REPL. Every function here takes a path or a frame; nothing reaches out to a
live telemetry backend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lab_35_operational_data_analysis.analysis import (
    ComparisonResult,
    ConfidenceInterval,
    RegressionResult,
    ReleaseSummary,
    chronological_releases,
    compare_releases,
    fit_duration_model,
    observations_to_frame,
    overall_confidence_interval,
    summarize_all_releases,
)
from lab_35_operational_data_analysis.report import (
    Environment,
    current_environment,
    render_html_report,
    render_svg_report,
)
from lab_35_operational_data_analysis.schema import LoadReport, load_observations


@dataclass(frozen=True)
class AnalysisReport:
    """Everything the web page shows, computed once from one CSV file."""

    source: Path
    generated_at: datetime
    load_report: LoadReport
    summaries: tuple[ReleaseSummary, ...]
    confidence: ConfidenceInterval
    comparison: ComparisonResult
    regression: RegressionResult
    svg: str
    environment: Environment

    def as_html(self) -> str:
        return render_html_report(
            # Only the file name, not the full path: the running server's
            # absolute filesystem layout is not something a report reader
            # needs and not something an error page should hand out either.
            source_label=self.source.name,
            generated_at_label=self.generated_at.isoformat(),
            valid_count=self.load_report.valid_count,
            rejected=self.load_report.rejected,
            duplicate_task_ids=self.load_report.duplicate_task_ids,
            summaries=self.summaries,
            confidence=self.confidence,
            comparison=self.comparison,
            regression=self.regression,
            svg=self.svg,
            environment=self.environment,
        )


def build_report(
    path: Path,
    *,
    now: Callable[[], datetime] | None = None,
) -> AnalysisReport:
    """Run the full analysis pipeline against *path*.

    *now* is injectable so tests can produce byte-identical reports; it
    defaults to the real UTC clock for normal use.
    """
    clock = now if now is not None else lambda: datetime.now(timezone.utc)
    load_report = load_observations(path)
    if not load_report.observations:
        raise ValueError(f"{path} produced no valid observations to analyse")

    frame = observations_to_frame(load_report.observations)
    summaries = summarize_all_releases(frame)
    confidence = overall_confidence_interval(frame)
    baseline, candidate = chronological_releases(frame)
    comparison = compare_releases(frame, baseline, candidate)
    regression = fit_duration_model(frame)
    svg = render_svg_report(frame, summaries)

    return AnalysisReport(
        source=path,
        generated_at=clock(),
        load_report=load_report,
        summaries=summaries,
        confidence=confidence,
        comparison=comparison,
        regression=regression,
        svg=svg,
        environment=current_environment(),
    )
