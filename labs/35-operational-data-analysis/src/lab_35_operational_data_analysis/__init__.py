"""Operational data analysis for relay: descriptive statistics, a confidence
interval, a two-release comparison and a basic regression, rendered as a
small local web report.

This is the checkpoint for Chapter 35 of Linux Distributed Systems
Programming with Python. It reads a CSV of completed relay task attempts
shaped like the telemetry Chapter 34 exports, validates it against a
documented schema, and answers a fixed set of operational questions with
NumPy, pandas, SciPy, Matplotlib and statsmodels. It does not train, score
or predict; that is out of scope for this book.
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
