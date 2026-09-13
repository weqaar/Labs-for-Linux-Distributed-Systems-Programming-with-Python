"""Read and interpret pyperf result artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyperf


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    name: str
    runs: int
    values: int
    mean_seconds: float
    median_seconds: float
    standard_deviation_seconds: float | None
    minimum_seconds: float
    maximum_seconds: float


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    baseline_seconds: float
    candidate_seconds: float
    speed_ratio: float
    percent_change: float
    exceeds_required_change: bool


def summarize_result(path: Path) -> tuple[BenchmarkSummary, ...]:
    """Load a pyperf JSON suite and retain both centre and spread."""

    suite = pyperf.BenchmarkSuite.load(str(path))
    summaries: list[BenchmarkSummary] = []
    for benchmark in suite.get_benchmarks():
        values = benchmark.get_values()
        summaries.append(
            BenchmarkSummary(
                name=benchmark.get_name(),
                runs=benchmark.get_nrun(),
                values=benchmark.get_nvalue(),
                mean_seconds=benchmark.mean(),
                median_seconds=benchmark.median(),
                standard_deviation_seconds=benchmark.stdev() if len(values) > 1 else None,
                minimum_seconds=min(values),
                maximum_seconds=max(values),
            )
        )
    return tuple(summaries)


def compare_medians(
    baseline_seconds: float,
    candidate_seconds: float,
    *,
    required_change: float,
) -> BenchmarkComparison:
    """Compare effect size without treating statistical significance as product value."""

    if baseline_seconds <= 0 or candidate_seconds <= 0:
        raise ValueError("benchmark medians must be positive")
    if required_change < 0:
        raise ValueError("required change cannot be negative")
    ratio = baseline_seconds / candidate_seconds
    percent_change = (candidate_seconds / baseline_seconds - 1.0) * 100.0
    return BenchmarkComparison(
        baseline_seconds=baseline_seconds,
        candidate_seconds=candidate_seconds,
        speed_ratio=ratio,
        percent_change=percent_change,
        exceeds_required_change=abs(percent_change) >= required_change,
    )
