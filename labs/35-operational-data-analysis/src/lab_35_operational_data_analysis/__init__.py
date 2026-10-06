"""Analyze recorded relay attempts and render a local web report.

Validate a CSV, summarize durations, compare two releases and fit a descriptive
regression with NumPy, pandas, SciPy, Matplotlib and statsmodels. The bundled
CSV is synthetic, not a live telemetry export. The analysis describes the
recorded sample rather than predicting future tasks.
"""

from __future__ import annotations

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
    summarize_release,
)
from lab_35_operational_data_analysis.pipeline import AnalysisReport, build_report
from lab_35_operational_data_analysis.schema import (
    LoadReport,
    Observation,
    RejectedRow,
    default_dataset_path,
    load_observations,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AnalysisReport",
    "build_report",
    "ComparisonResult",
    "ConfidenceInterval",
    "RegressionResult",
    "ReleaseSummary",
    "LoadReport",
    "Observation",
    "RejectedRow",
    "chronological_releases",
    "compare_releases",
    "default_dataset_path",
    "fit_duration_model",
    "load_observations",
    "observations_to_frame",
    "overall_confidence_interval",
    "summarize_all_releases",
    "summarize_release",
]
