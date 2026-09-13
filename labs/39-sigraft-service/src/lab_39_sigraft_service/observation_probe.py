"""Collect a bounded synthetic comparison through the public SigRaft API."""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic_ns
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Response(Protocol):
    def __enter__(self) -> Response: ...

    def __exit__(self, *args: object) -> None: ...

    def read(self) -> bytes: ...


OpenRequest = Callable[[Request, float], Response]


def _open_request(request: Request, timeout: float) -> Response:
    return urlopen(request, timeout=timeout)


def collect_observations(
    baseline_url: str,
    candidate_url: str,
    output: Path,
    *,
    count: int = 20,
    open_request: OpenRequest = _open_request,
    clock: Callable[[], int] = monotonic_ns,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Exercise both public endpoints and write one fixed-schema CSV."""

    if not 10 <= count <= 1_000:
        raise ValueError("count must be between 10 and 1000 per release")
    rows: list[dict[str, object]] = []
    for release, base_url in (("baseline", baseline_url), ("candidate", candidate_url)):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("probe URLs must use HTTP or HTTPS")
        for index in range(1, count + 1):
            started = clock()
            task_id = f"probe-{release}-{index}"
            outcome = "failed"
            try:
                request = Request(
                    f"{base_url.rstrip('/')}/tasks",
                    data=json.dumps({"action": f"release-probe-{index}"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with open_request(request, 10.0) as response:
                    payload = json.loads(response.read())
                if isinstance(payload, dict) and isinstance(payload.get("task_id"), str):
                    task_id = payload["task_id"]
                    outcome = "succeeded"
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
                outcome = "failed"
            elapsed_ms = max((clock() - started) / 1_000_000, 0.001)
            rows.append(
                {
                    "timestamp": now().astimezone(timezone.utc).isoformat(),
                    "task_id": task_id,
                    "release": release,
                    "region": "release-probe",
                    "queue_depth": 0,
                    "duration_ms": f"{elapsed_ms:.3f}",
                    "outcome": outcome,
                    "attempt": 1,
                }
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "timestamp",
                "task_id",
                "release",
                "region",
                "queue_depth",
                "duration_ms",
                "outcome",
                "attempt",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect release observations")
    parser.add_argument("--baseline-url", required=True)
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--count", type=int, default=20)
    arguments = parser.parse_args(argv)
    collect_observations(
        arguments.baseline_url,
        arguments.candidate_url,
        arguments.output,
        count=arguments.count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
