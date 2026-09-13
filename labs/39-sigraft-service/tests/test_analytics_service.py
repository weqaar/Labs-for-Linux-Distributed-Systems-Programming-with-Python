"""Tests for final SigRaft operational analysis and refresh."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import pandas as pd
import pytest

from lab_39_sigraft_service.analytics_service import (
    AnalysisError,
    AnalyticsRuntime,
    build_analysis_report,
)
from lab_39_sigraft_service.config import AnalysisSettings, ConfigManager
from lab_39_sigraft_service.sigraft_service import SigRaftService, run_server

DIGEST = "sha256:" + "a" * 64


def test_packaged_observations_produce_all_statistics_and_deterministic_report() -> None:
    generated = datetime(2026, 2, 3, tzinfo=timezone.utc)

    first = build_analysis_report("package:observations.csv", now=generated)
    second = build_analysis_report("package:observations.csv", now=generated)

    assert first == second
    assert first.valid_rows == 20
    assert [summary.release for summary in first.summaries] == ["baseline", "candidate"]
    assert first.summaries[1].p95_ms < first.summaries[0].p95_ms
    assert all(math.isfinite(value) for value in first.mean_ci)
    assert math.isfinite(first.mann_whitney_p)
    assert math.isfinite(first.regression_r_squared)
    assert "<svg" in first.html
    assert "NumPy" in first.html
    assert "statsmodels" in first.html


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1.0])
def test_analysis_rejects_nonfinite_or_nonpositive_duration(
    tmp_path: Path, duration: float
) -> None:
    frame = pd.read_csv(
        Path(__file__).parents[1] / "src/lab_39_sigraft_service/data/observations.csv"
    )
    frame["duration_ms"] = frame["duration_ms"].astype(float)
    frame.loc[0, "duration_ms"] = duration
    path = tmp_path / "bad.csv"
    frame.to_csv(path, index=False)

    with pytest.raises(AnalysisError):
        build_analysis_report(str(path))


def test_analysis_configuration_prepares_before_publishing() -> None:
    manager = ConfigManager.from_bytes(
        b"""[schema]
version=1
[identity]
deployment_environment="test"
service_instance_id="test"
[requests]
max_body_bytes=4096
max_action_chars=100
[telemetry]
sample_ratio=1.0
exporter="memory"
otlp_endpoint="http://collector:4317"
otlp_insecure=true
[analysis]
enabled=true
observations_path="package:observations.csv"
refresh_interval_seconds=60.0
"""
    )
    runtime = AnalyticsRuntime(manager.snapshot.settings.analysis)
    manager.register(runtime)
    disabled = b"""[schema]
version=1
[identity]
deployment_environment="test"
service_instance_id="test"
[requests]
max_body_bytes=4096
max_action_chars=100
[telemetry]
sample_ratio=1.0
exporter="memory"
otlp_endpoint="http://collector:4317"
otlp_insecure=true
[analysis]
enabled=false
observations_path="package:observations.csv"
refresh_interval_seconds=120.0
"""

    assert runtime.report is not None
    assert manager.reload_bytes(disabled)
    assert runtime.report is None
    assert runtime.metadata()["enabled"] is False


def test_integrated_analysis_route_has_safe_headers() -> None:
    hosted = run_server(SigRaftService(DIGEST))
    try:
        with urlopen(f"{hosted.base_url}/analysis") as response:
            body = response.read().decode()
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert "SigRaft operational analysis" in body
    finally:
        hosted.close()


def test_refresh_thread_stops_without_waiting_for_interval() -> None:
    runtime = AnalyticsRuntime(AnalysisSettings(True, "package:observations.csv", 86_400.0))

    runtime.start()
    runtime.stop()
