"""Descriptive statistics, a confidence interval, a two-release comparison
and a basic regression for relay operational observations.

This module answers a fixed set of operational questions with NumPy,
pandas, SciPy and statsmodels, and stops there. Predicting a task's outcome
or duration from features is a modelling exercise outside the scope this
book sets: these functions compute the arithmetic behind a promotion or
rollback decision, not a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.stattools import durbin_watson

from lab_35_operational_data_analysis.schema import Observation


@dataclass(frozen=True)
class ReleaseSummary:
    """Descriptive statistics for the duration of one release's tasks."""

    release: str
    count: int
    failures: int
    failure_rate: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    sample_std_ms: float


@dataclass(frozen=True)
class ConfidenceInterval:
    """A confidence interval for a population mean, not a percentage."""

    mean_ms: float
    lower_ms: float
    upper_ms: float
    confidence: float
    sample_size: int


@dataclass(frozen=True)
class ComparisonResult:
    """A two-release Mann-Whitney comparison and its effect size.

    ``rank_biserial`` is positive when the candidate release tends to have
    the larger durations of the two, in the range -1 to 1. It answers "how
    separated are the two distributions", which a p-value alone does not.
    """

    baseline_release: str
    candidate_release: str
    baseline_n: int
    candidate_n: int
    u_statistic: float
    p_value: float
    rank_biserial: float


@dataclass(frozen=True)
class RegressionResult:
    """An OLS fit of duration against queue depth and release.

    ``queue_depth_coefficient_with_region_control_ms`` refits the same
    question with region added. The two queue-depth coefficients are kept
    side by side because region is a plausible confounder here: it is
    correlated with both queue depth and duration, so an omitted-variable
    check is part of a basic reading of this regression, not an extra.
    """

    intercept_ms: float
    queue_depth_coefficient_ms: float
    release_coefficients_ms: dict[str, float]
    r_squared: float
    durbin_watson: float
    residual_std_ms: float
    queue_depth_coefficient_with_region_control_ms: float


def observations_to_frame(observations: tuple[Observation, ...]) -> pd.DataFrame:
    """Build a tidy frame: one row per observation, one column per field.

    Tidy here means what Chapter 35 defines it to mean: every row is one
    observation unit (one task attempt), every column is one variable, and
    no value is packed into a column name or spread across several cells.
    """
    if not observations:
        raise ValueError("cannot analyse an empty set of observations")
    return pd.DataFrame(
        {
            "timestamp": [o.timestamp for o in observations],
            "task_id": [o.task_id for o in observations],
            "release": [o.release for o in observations],
            "region": [o.region for o in observations],
            "queue_depth_at_submit": [o.queue_depth_at_submit for o in observations],
            "duration_ms": [o.duration_ms for o in observations],
            "outcome": [o.outcome for o in observations],
            "attempt": [o.attempt for o in observations],
        }
    )


def _releases_ordered_by_first_timestamp(frame: pd.DataFrame) -> list[str]:
    """Release names ordered by when each release first appears in the frame."""
    first_seen = cast(pd.Series, frame.groupby("release")["timestamp"].min())
    return [str(release) for release in first_seen.sort_values().index]


def chronological_releases(frame: pd.DataFrame) -> tuple[str, str]:
    """Return (baseline, candidate) ordered by each release's first timestamp.

    The comparison this lab draws is deliberately narrow: exactly two
    releases, ordered by when they started serving. A frame with any other
    number of releases raises rather than guessing which two to compare.
    """
    releases = _releases_ordered_by_first_timestamp(frame)
    if len(releases) != 2:
        raise ValueError(
            "the two-release comparison needs exactly two releases, "
            f"found {len(releases)}: {releases}"
        )
    return releases[0], releases[1]


def summarize_release(frame: pd.DataFrame, release: str) -> ReleaseSummary:
    """Compute count, failure rate and duration statistics for one release."""
    rows = frame.loc[frame["release"] == release]
    if rows.empty:
        raise ValueError(f"no observations for release {release!r}")
    durations = rows["duration_ms"].to_numpy(dtype=np.float64)
    count = int(durations.shape[0])
    failures = int((rows["outcome"] == "failed").sum())
    return ReleaseSummary(
        release=release,
        count=count,
        failures=failures,
        failure_rate=failures / count,
        mean_ms=float(durations.mean()),
        median_ms=float(np.median(durations)),
        p95_ms=float(np.percentile(durations, 95)),
        p99_ms=float(np.percentile(durations, 99)),
        # ddof=1: the sample standard deviation, since these releases are a
        # sample of tasks rather than the entire population of tasks that
        # will ever run. NumPy's own default is ddof=0 and would understate
        # it, which is the trap Chapter 35 names explicitly.
        sample_std_ms=float(durations.std(ddof=1)),
    )


def summarize_all_releases(frame: pd.DataFrame) -> tuple[ReleaseSummary, ...]:
    """Summarize every release present, ordered by first timestamp."""
    order = _releases_ordered_by_first_timestamp(frame)
    return tuple(summarize_release(frame, release) for release in order)


def overall_confidence_interval(
    frame: pd.DataFrame, confidence: float = 0.95
) -> ConfidenceInterval:
    """A t-distribution confidence interval for the mean task duration.

    This is an interval for the mean, built from sample size and spread. It
    is not a percentile and it is not a guarantee about any single task.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    values = frame["duration_ms"].to_numpy(dtype=np.float64)
    n = values.shape[0]
    if n < 2:
        raise ValueError("need at least two observations for a confidence interval")
    mean = float(values.mean())
    sem = float(stats.sem(values))
    lower, upper = stats.t.interval(confidence, df=n - 1, loc=mean, scale=sem)
    return ConfidenceInterval(
        mean_ms=mean,
        lower_ms=float(lower),
        upper_ms=float(upper),
        confidence=confidence,
        sample_size=n,
    )


def compare_releases(frame: pd.DataFrame, baseline: str, candidate: str) -> ComparisonResult:
    """Compare two releases' durations with a Mann-Whitney U test.

    Mann-Whitney is used in preference to a two-sample t-test because task
    duration is right-skewed (a handful of very slow tasks and a hard floor
    near zero), which violates the roughly symmetric, similarly-shaped
    samples a t-test assumes. Mann-Whitney only assumes independent
    observations and an ordinal scale.
    """
    base = frame.loc[frame["release"] == baseline, "duration_ms"].to_numpy(dtype=np.float64)
    cand = frame.loc[frame["release"] == candidate, "duration_ms"].to_numpy(dtype=np.float64)
    if base.size == 0 or cand.size == 0:
        raise ValueError("both releases need at least one observation to compare")
    result = stats.mannwhitneyu(base, cand, alternative="two-sided")
    u_statistic = float(result.statistic)
    # The rank-biserial correlation turns U into a signed effect size on a
    # -1..1 scale: 0 means the two samples are interleaved with no tendency
    # either way, and +-1 means every candidate observation beat (or lost
    # to) every baseline observation.
    rank_biserial = 1.0 - (2.0 * u_statistic) / (base.size * cand.size)
    return ComparisonResult(
        baseline_release=baseline,
        candidate_release=candidate,
        baseline_n=int(base.size),
        candidate_n=int(cand.size),
        u_statistic=u_statistic,
        p_value=float(result.pvalue),
        rank_biserial=float(rank_biserial),
    )


def fit_duration_model(frame: pd.DataFrame) -> RegressionResult:
    """Fit duration against queue depth and release with ordinary least squares.

    The design matrix has an intercept column of ones, one numeric column
    for queue depth, and one dummy column per release after the first
    (statsmodels' formula interface builds this from ``C(release)``
    automatically, absorbing the first release into the intercept). The
    coefficients describe an association in this sample, not a causal
    effect: a second fit adding
    region is kept alongside the first because region is correlated with
    both queue depth and duration in this dataset, and dropping it changes
    even the sign of the queue-depth coefficient. Durbin-Watson checks
    whether the residuals are still correlated with each other in time
    order, which plain OLS standard errors assume they are not.
    """
    model = smf.ols("duration_ms ~ queue_depth_at_submit + C(release)", data=frame).fit()
    region_controlled = smf.ols(
        "duration_ms ~ queue_depth_at_submit + C(release) + C(region)", data=frame
    ).fit()
    release_coefficients = {
        name[len("C(release)[T.") : -1]: float(value)
        for name, value in model.params.items()
        if name.startswith("C(release)[T.")
    }
    return RegressionResult(
        intercept_ms=float(model.params["Intercept"]),
        queue_depth_coefficient_ms=float(model.params["queue_depth_at_submit"]),
        release_coefficients_ms=release_coefficients,
        r_squared=float(model.rsquared),
        durbin_watson=float(durbin_watson(model.resid.to_numpy())),
        residual_std_ms=float(model.resid.std(ddof=1)),
        queue_depth_coefficient_with_region_control_ms=float(
            region_controlled.params["queue_depth_at_submit"]
        ),
    )
