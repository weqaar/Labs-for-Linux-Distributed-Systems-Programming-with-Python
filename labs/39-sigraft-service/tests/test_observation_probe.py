"""Tests for bounded release observation collection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request

import pandas as pd
import pytest

from lab_39_sigraft_service.analytics_service import build_analysis_report
from lab_39_sigraft_service.observation_probe import collect_observations


class FakeResponse:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return f'{{"task_id":"{self.task_id}"}}'.encode()


class FakeEndpoints:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: Request, timeout: float) -> FakeResponse:
        assert timeout == 10.0
        assert request.full_url.endswith("/tasks")
        self.calls.append(request.full_url)
        release = "baseline" if "baseline" in request.full_url else "candidate"
        return FakeResponse(f"task-{release}-{len(self.calls)}")


class ScriptedClock:
    def __init__(self) -> None:
        self.value = 0
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        if self.calls % 2 == 0:
            self.value += ((self.calls // 2) % 5 + 1) * 1_000_000
        return self.value


def test_probe_collects_both_public_endpoints_for_analysis(tmp_path: Path) -> None:
    endpoints = FakeEndpoints()
    output = tmp_path / "observations.csv"

    collect_observations(
        "https://baseline.example",
        "https://candidate.example",
        output,
        count=10,
        open_request=endpoints,
        clock=ScriptedClock(),
        now=lambda: datetime(2026, 2, 3, tzinfo=timezone.utc),
    )

    frame = pd.read_csv(output)
    assert len(frame.index) == 20
    assert frame.groupby("release").size().to_dict() == {"baseline": 10, "candidate": 10}
    assert set(frame["outcome"]) == {"succeeded"}
    assert len(endpoints.calls) == 20
    report = build_analysis_report(str(output))
    assert report.valid_rows == 20


def test_probe_rejects_unbounded_counts_and_non_http_urls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="between 10 and 1000"):
        collect_observations("https://a", "https://b", tmp_path / "x.csv", count=9)
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        collect_observations(
            "file:///tmp/baseline",
            "https://candidate.example",
            tmp_path / "x.csv",
            count=10,
        )
