"""Tests for descriptive statistics, confidence interval, comparison and OLS."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy import stats

from lab_35_operational_data_analysis.analysis import (
    chronological_releases,
    compare_releases,
    fit_duration_model,
    observations_to_frame,
    overall_confidence_interval,
    summarize_release,
)
from lab_35_operational_data_analysis.schema import Observation

EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_observation(
    *,
    offset_seconds: int,
    task_id: str,
    release: str,
    region: str = "eastus",
    queue_depth: int = 0,
    duration_ms: float,
    outcome: str = "succeeded",
    attempt: int = 1,
) -> Observation:
    return Observation(
        timestamp=EPOCH + timedelta(seconds=offset_seconds),
        task_id=task_id,
        release=release,
        region=region,
        queue_depth_at_submit=queue_depth,
        duration_ms=duration_ms,
        outcome=outcome,
        attempt=attempt,
    )


def test_observations_to_frame_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="empty"):
        observations_to_frame(())


def test_summarize_release_matches_hand_computed_statistics() -> None:
    durations = [10.0, 20.0, 30.0, 40.0, 100.0]
    observations = tuple(
        make_observation(
            offset_seconds=i,
            task_id=f"task-{i}",
            release="rel-1",
            duration_ms=value,
            outcome="failed" if i == 0 else "succeeded",
        )
        for i, value in enumerate(durations)
    )
    frame = observations_to_frame(observations)
    summary = summarize_release(frame, "rel-1")

    array = np.array(durations)
    assert summary.count == 5
    assert summary.failures == 1
    assert summary.failure_rate == pytest.approx(0.2)
    assert summary.mean_ms == pytest.approx(array.mean())
    assert summary.median_ms == pytest.approx(30.0)
    assert summary.p95_ms == pytest.approx(np.percentile(array, 95))
    assert summary.p99_ms == pytest.approx(np.percentile(array, 99))
    # ddof=1: the sample standard deviation, not the ddof=0 population one.
    assert summary.sample_std_ms == pytest.approx(array.std(ddof=1))
    assert summary.sample_std_ms != pytest.approx(array.std(ddof=0))


def test_summarize_release_raises_for_an_unknown_release() -> None:
    observations = (
        make_observation(offset_seconds=0, task_id="t1", release="rel-1", duration_ms=1.0),
    )
    frame = observations_to_frame(observations)
    with pytest.raises(ValueError, match="no observations"):
        summarize_release(frame, "rel-missing")


def test_chronological_releases_orders_by_first_timestamp() -> None:
    observations = (
        make_observation(offset_seconds=100, task_id="t1", release="second", duration_ms=1.0),
        make_observation(offset_seconds=0, task_id="t2", release="first", duration_ms=1.0),
        make_observation(offset_seconds=50, task_id="t3", release="second", duration_ms=1.0),
    )
    frame = observations_to_frame(observations)
    assert chronological_releases(frame) == ("first", "second")


def test_chronological_releases_rejects_anything_but_two() -> None:
    observations = (
        make_observation(offset_seconds=0, task_id="t1", release="only-one", duration_ms=1.0),
    )
    frame = observations_to_frame(observations)
    with pytest.raises(ValueError, match="exactly two releases"):
        chronological_releases(frame)


def test_overall_confidence_interval_matches_scipy_directly() -> None:
    rng = np.random.default_rng(1)
    durations = rng.normal(loc=200.0, scale=20.0, size=50)
    observations = tuple(
        make_observation(offset_seconds=i, task_id=f"t{i}", release="rel-1", duration_ms=float(d))
        for i, d in enumerate(durations)
    )
    frame = observations_to_frame(observations)
    interval = overall_confidence_interval(frame, confidence=0.95)

    mean = durations.mean()
    sem = stats.sem(durations)
    expected_lower, expected_upper = stats.t.interval(
        0.95, df=len(durations) - 1, loc=mean, scale=sem
    )
    assert interval.mean_ms == pytest.approx(mean)
    assert interval.lower_ms == pytest.approx(expected_lower)
    assert interval.upper_ms == pytest.approx(expected_upper)
    assert interval.lower_ms < interval.mean_ms < interval.upper_ms
    assert interval.sample_size == 50


def test_confidence_interval_requires_at_least_two_observations() -> None:
    observations = (
        make_observation(offset_seconds=0, task_id="t1", release="rel-1", duration_ms=1.0),
    )
    frame = observations_to_frame(observations)
    with pytest.raises(ValueError, match="at least two"):
        overall_confidence_interval(frame)


def test_confidence_interval_rejects_an_invalid_confidence_level() -> None:
    observations = tuple(
        make_observation(
            offset_seconds=i, task_id=f"t{i}", release="rel-1", duration_ms=float(i + 1)
        )
        for i in range(5)
    )
    frame = observations_to_frame(observations)
    with pytest.raises(ValueError, match="between 0 and 1"):
        overall_confidence_interval(frame, confidence=1.5)


def test_compare_releases_detects_a_clear_separation() -> None:
    baseline = tuple(
        make_observation(
            offset_seconds=i, task_id=f"base-{i}", release="baseline", duration_ms=float(i)
        )
        for i in range(30)
    )
    candidate = tuple(
        make_observation(
            offset_seconds=1000 + i,
            task_id=f"cand-{i}",
            release="candidate",
            duration_ms=float(i + 500),
        )
        for i in range(30)
    )
    frame = observations_to_frame(baseline + candidate)
    result = compare_releases(frame, "baseline", "candidate")

    assert result.baseline_n == 30
    assert result.candidate_n == 30
    assert result.p_value < 0.001
    # Candidate durations are all larger, so the rank-biserial effect size
    # should sit close to its extreme of +1.
    assert result.rank_biserial == pytest.approx(1.0, abs=0.05)


def test_compare_releases_reports_no_effect_for_identical_distributions() -> None:
    rng = np.random.default_rng(7)
    values = rng.normal(loc=200.0, scale=10.0, size=60)
    baseline = tuple(
        make_observation(
            offset_seconds=i, task_id=f"base-{i}", release="baseline", duration_ms=float(v)
        )
        for i, v in enumerate(values[:30])
    )
    candidate = tuple(
        make_observation(
            offset_seconds=1000 + i, task_id=f"cand-{i}", release="candidate", duration_ms=float(v)
        )
        for i, v in enumerate(values[30:])
    )
    frame = observations_to_frame(baseline + candidate)
    result = compare_releases(frame, "baseline", "candidate")
    assert abs(result.rank_biserial) < 0.5


def test_compare_releases_requires_both_releases_present() -> None:
    observations = (
        make_observation(offset_seconds=0, task_id="t1", release="only", duration_ms=1.0),
    )
    frame = observations_to_frame(observations)
    with pytest.raises(ValueError, match="both releases"):
        compare_releases(frame, "only", "missing")


def test_fit_duration_model_recovers_a_known_linear_relationship() -> None:
    rng = np.random.default_rng(42)
    n_per_group = 200
    rows = []
    for release, release_shift in (("rel-a", 0.0), ("rel-b", 15.0)):
        for region, region_shift in (("eastus", 0.0), ("westeurope", 8.0)):
            for _ in range(n_per_group):
                queue_depth = int(rng.integers(0, 20))
                noise = rng.normal(scale=2.0)
                duration = 100.0 + 3.0 * queue_depth + release_shift + region_shift + noise
                rows.append((release, region, queue_depth, max(duration, 0.1)))
    # Shuffled into arrival order so row order carries no information about
    # release or region. Built in block order, consecutive rows would share
    # the same omitted-region bias and Durbin-Watson would flag artificial
    # autocorrelation that has nothing to do with the data-generating process
    # being tested.
    rng.shuffle(rows)
    observations = tuple(
        make_observation(
            offset_seconds=idx,
            task_id=f"t{idx}",
            release=release,
            region=region,
            queue_depth=queue_depth,
            duration_ms=duration_ms,
        )
        for idx, (release, region, queue_depth, duration_ms) in enumerate(rows)
    )
    frame = observations_to_frame(observations)
    result = fit_duration_model(frame)

    # The primary equation omits region on purpose (see fit_duration_model's
    # docstring), and region is balanced evenly across queue depth here, so
    # its average effect (0 and 8 ms, split evenly) is absorbed into the
    # intercept rather than biasing the queue-depth or release coefficients.
    assert result.intercept_ms == pytest.approx(104.0, abs=3.0)
    assert result.queue_depth_coefficient_ms == pytest.approx(3.0, abs=0.5)
    assert result.release_coefficients_ms["rel-b"] == pytest.approx(15.0, abs=3.0)
    assert result.r_squared > 0.9
    assert 1.5 < result.durbin_watson < 2.5
    # With region also in the true generative model but excluded from this
    # equation's variables, the queue-depth estimate should stay close to
    # the same coefficient once region is added back as a control, because
    # queue depth here is independent of region by construction.
    assert result.queue_depth_coefficient_with_region_control_ms == pytest.approx(3.0, abs=0.5)


def test_fit_duration_model_on_the_bundled_fixture_shows_the_confound() -> None:
    from lab_35_operational_data_analysis.schema import default_dataset_path, load_observations

    report = load_observations(default_dataset_path())
    frame = observations_to_frame(report.observations)
    result = fit_duration_model(frame)

    # Documented in the chapter: omitting region from this particular sample
    # flips the sign of the queue-depth coefficient, which is why the report
    # keeps both numbers rather than only the first fit.
    assert result.queue_depth_coefficient_ms < 0
    assert result.queue_depth_coefficient_with_region_control_ms > 0
