"""Operational analysis report and reloadable refresh worker for SigRaft."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from threading import Event, RLock, Thread

import matplotlib
import numpy as np
import pandas as pd
import scipy
import statsmodels
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.stattools import durbin_watson

from lab_39_sigraft_service.config import AnalysisSettings, PreparedChange, ServiceSettings

matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "sigraft-operational-analysis"
from matplotlib import pyplot as plt  # noqa: E402

log = logging.getLogger(__name__)

_COLUMNS = (
    "timestamp",
    "task_id",
    "release",
    "region",
    "queue_depth",
    "duration_ms",
    "outcome",
    "attempt",
)
_PLOT_LOCK = RLock()
_MAX_INPUT_BYTES = 20 * 1024 * 1024
_MAX_ROWS = 100_000


class AnalysisError(ValueError):
    """Raised when observations cannot produce a trustworthy report."""


@dataclass(frozen=True)
class ReleaseStatistics:
    """Bounded descriptive statistics for one release."""

    release: str
    count: int
    failure_rate: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    sample_stddev_ms: float


@dataclass(frozen=True)
class AnalysisReport:
    """One immutable report generated from one complete observation snapshot."""

    revision: str
    generated_at: str
    valid_rows: int
    summaries: tuple[ReleaseStatistics, ...]
    mean_ci: tuple[float, float]
    mann_whitney_p: float
    rank_biserial_candidate_slower: float
    regression_r_squared: float
    durbin_watson: float
    html: str

    def as_metadata(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "generated_at": self.generated_at,
            "valid_rows": self.valid_rows,
        }


def build_analysis_report(
    path: str,
    *,
    base_dir: Path | None = None,
    now: datetime | None = None,
) -> AnalysisReport:
    """Load, validate, analyse, and render one bounded CSV snapshot."""

    content = _read_observations(path, base_dir)
    try:
        frame = pd.read_csv(io.BytesIO(content))
    except (pd.errors.ParserError, UnicodeDecodeError) as error:
        raise AnalysisError("observations are not valid CSV") from error
    if tuple(frame.columns) != _COLUMNS:
        raise AnalysisError("observations must use the documented columns and order")
    if frame.empty or len(frame.index) > _MAX_ROWS:
        raise AnalysisError(f"observations must contain between 1 and {_MAX_ROWS} rows")

    for name in ("queue_depth", "duration_ms", "attempt"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    numeric = frame[["queue_depth", "duration_ms", "attempt"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise AnalysisError("numeric observations must be finite")
    if np.any(numeric[:, 0] < 0) or np.any(numeric[:, 1] <= 0):
        raise AnalysisError("queue depth and duration are outside their valid ranges")
    if np.any(numeric[:, 2] < 1) or np.any(numeric[:, 2] % 1 != 0):
        raise AnalysisError("attempt must be a positive integer")
    if not frame["outcome"].isin(("succeeded", "failed")).all():
        raise AnalysisError("outcome must be succeeded or failed")
    if np.any(frame[["task_id", "release", "region"]].isna().to_numpy()):
        raise AnalysisError("identifier fields must not be empty")
    for name in ("task_id", "release", "region"):
        values = frame[name].astype(str)
        if values.str.len().gt(64).any() or not values.str.fullmatch(r"[A-Za-z0-9._-]+").all():
            raise AnalysisError(f"{name} contains an unsafe identifier")
        frame[name] = values
    timestamp_text = frame["timestamp"].astype(str)
    if not timestamp_text.str.fullmatch(r".*(?:Z|[+-]\d{2}:\d{2})").all():
        raise AnalysisError("timestamps must include an explicit UTC offset")
    try:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
    except ValueError as error:
        raise AnalysisError("timestamps must be ISO-8601 values with UTC offsets") from error

    release_starts = [
        (str(release), pd.Timestamp(group["timestamp"].min()))
        for release, group in frame.groupby("release")
    ]
    releases = tuple(release for release, _ in sorted(release_starts, key=lambda item: item[1]))
    if len(releases) != 2:
        raise AnalysisError("release comparison requires exactly two releases")
    summaries = tuple(_release_statistics(frame, release) for release in releases)
    durations = frame["duration_ms"].to_numpy(dtype=float)
    if len(durations) < 4 or float(np.std(durations, ddof=1)) == 0.0:
        raise AnalysisError("analysis requires at least four observations with varying duration")
    standard_error = stats.sem(durations)
    interval = stats.t.interval(
        0.95,
        df=len(durations) - 1,
        loc=float(np.mean(durations)),
        scale=float(standard_error),
    )
    baseline = frame.loc[frame["release"] == releases[0], "duration_ms"].to_numpy(dtype=float)
    candidate = frame.loc[frame["release"] == releases[1], "duration_ms"].to_numpy(dtype=float)
    if len(baseline) < 2 or len(candidate) < 2:
        raise AnalysisError("each release requires at least two observations")
    comparison = stats.mannwhitneyu(baseline, candidate, alternative="two-sided")
    rank_biserial = 1.0 - (2.0 * float(comparison.statistic)) / (len(baseline) * len(candidate))

    design = pd.get_dummies(
        frame[["queue_depth", "release", "region"]],
        columns=["release", "region"],
        drop_first=True,
        dtype=float,
    )
    model = sm.OLS(
        frame["duration_ms"].to_numpy(dtype=float),
        sm.add_constant(design.to_numpy(dtype=float)),
    ).fit()
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    revision = hashlib.sha256(content).hexdigest()
    svg = _render_svg(frame, summaries)
    html = _render_html(
        revision=revision,
        generated_at=generated_at,
        summaries=summaries,
        mean_ci=(float(interval[0]), float(interval[1])),
        mann_whitney_p=float(comparison.pvalue),
        rank_biserial=rank_biserial,
        r_squared=float(model.rsquared),
        residual_durbin_watson=float(durbin_watson(model.resid)),
        svg=svg,
    )
    return AnalysisReport(
        revision=revision,
        generated_at=generated_at,
        valid_rows=len(frame.index),
        summaries=summaries,
        mean_ci=(float(interval[0]), float(interval[1])),
        mann_whitney_p=float(comparison.pvalue),
        rank_biserial_candidate_slower=rank_biserial,
        regression_r_squared=float(model.rsquared),
        durbin_watson=float(durbin_watson(model.resid)),
        html=html,
    )


def _read_observations(path: str, base_dir: Path | None) -> bytes:
    if path == "package:observations.csv":
        content = (
            resources.files("lab_39_sigraft_service")
            .joinpath("data")
            .joinpath("observations.csv")
            .read_bytes()
        )
    else:
        candidate = Path(path)
        if not candidate.is_absolute() and base_dir is not None:
            candidate = base_dir / candidate
        try:
            content = candidate.read_bytes()
        except OSError as error:
            raise AnalysisError("observations could not be read") from error
    if len(content) > _MAX_INPUT_BYTES:
        raise AnalysisError("observations exceed the configured analysis boundary")
    return content


def _release_statistics(frame: pd.DataFrame, release: str) -> ReleaseStatistics:
    selected = frame.loc[frame["release"] == release]
    durations = selected["duration_ms"].to_numpy(dtype=float)
    return ReleaseStatistics(
        release=release,
        count=len(selected.index),
        failure_rate=float((selected["outcome"] == "failed").mean()),
        mean_ms=float(np.mean(durations)),
        median_ms=float(np.median(durations)),
        p95_ms=float(np.percentile(durations, 95)),
        p99_ms=float(np.percentile(durations, 99)),
        sample_stddev_ms=float(np.std(durations, ddof=1)),
    )


def _render_svg(frame: pd.DataFrame, summaries: tuple[ReleaseStatistics, ...]) -> str:
    with _PLOT_LOCK:
        figure, axes = plt.subplots(1, 2, figsize=(9, 3.3), constrained_layout=True)
        for summary in summaries:
            values = frame.loc[frame["release"] == summary.release, "duration_ms"]
            axes[0].hist(values, bins=6, alpha=0.55, label=summary.release)
        axes[0].set(title="Observed duration", xlabel="Milliseconds", ylabel="Tasks")
        axes[0].legend()
        axes[1].scatter(frame["queue_depth"], frame["duration_ms"], alpha=0.7)
        axes[1].set(title="Queue depth and duration", xlabel="Queue depth", ylabel="Milliseconds")
        output = io.StringIO()
        figure.savefig(
            output,
            format="svg",
            metadata={"Date": None, "Creator": "SigRaft analysis"},
        )
        plt.close(figure)
    svg = output.getvalue()
    return svg[svg.index("<svg") :]


def _render_html(
    *,
    revision: str,
    generated_at: str,
    summaries: tuple[ReleaseStatistics, ...],
    mean_ci: tuple[float, float],
    mann_whitney_p: float,
    rank_biserial: float,
    r_squared: float,
    residual_durbin_watson: float,
    svg: str,
) -> str:
    rows = "".join(
        "<tr>"
        f"<th scope='row'>{escape(item.release)}</th><td>{item.count}</td>"
        f"<td>{item.failure_rate:.1%}</td><td>{item.mean_ms:.1f}</td>"
        f"<td>{item.median_ms:.1f}</td><td>{item.p95_ms:.1f}</td>"
        f"<td>{item.p99_ms:.1f}</td><td>{item.sample_stddev_ms:.1f}</td>"
        "</tr>"
        for item in summaries
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SigRaft operational analysis</title>
<style>body{{font:16px sans-serif;max-width:70rem;margin:auto;padding:1rem}}
table{{border-collapse:collapse}}th,td{{border:1px solid #777;padding:.4rem;text-align:right}}
th:first-child{{text-align:left}}svg{{max-width:100%;height:auto}}</style></head>
<body><h1>SigRaft operational analysis</h1>
<p>Generated <time>{escape(generated_at)}</time> from revision <code>{revision}</code>.</p>
<table><caption>Observed releases</caption><thead><tr><th>Release</th><th>Count</th>
<th>Failures</th><th>Mean</th><th>Median</th><th>p95</th><th>p99</th>
<th>Sample SD</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Inference and diagnostics</h2><dl>
<dt>95% t interval for the overall mean</dt><dd>{mean_ci[0]:.2f} to {mean_ci[1]:.2f} ms</dd>
<dt>Mann-Whitney two-sided p-value</dt><dd>{mann_whitney_p:.4g}</dd>
<dt>Rank-biserial effect, positive means candidate slower</dt><dd>{rank_biserial:.3f}</dd>
<dt>OLS R-squared</dt><dd>{r_squared:.3f}</dd>
<dt>Durbin-Watson residual diagnostic</dt><dd>{residual_durbin_watson:.3f}</dd>
</dl>{svg}<footer><p>Libraries: NumPy {np.__version__}, pandas {pd.__version__},
Matplotlib {matplotlib.__version__}, SciPy {scipy.__version__}, statsmodels
{statsmodels.__version__}.</p></footer></body></html>"""


@dataclass
class _AnalysisChange:
    runtime: AnalyticsRuntime
    old_settings: AnalysisSettings
    old_report: AnalysisReport | None
    new_settings: AnalysisSettings
    new_report: AnalysisReport | None

    def commit(self) -> None:
        self.runtime._publish(self.new_settings, self.new_report)

    def rollback(self) -> None:
        self.runtime._publish(self.old_settings, self.old_report)


class AnalyticsRuntime:
    """Managed report refresh thread and configuration reload participant."""

    name = "operational-analysis"

    def __init__(self, settings: AnalysisSettings, *, base_dir: Path | None = None) -> None:
        self._lock = RLock()
        self._settings = settings
        self._base_dir = base_dir
        self._report = self._build(settings)
        self._last_result = "initial"
        self._last_error: str | None = None
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None

    @property
    def report(self) -> AnalysisReport | None:
        with self._lock:
            return self._report

    def metadata(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self._settings.enabled,
                "last_result": self._last_result,
                "last_error": self._last_error,
                "report": self._report.as_metadata() if self._report else None,
            }

    def prepare(self, candidate: ServiceSettings, current: ServiceSettings) -> PreparedChange:
        del current
        with self._lock:
            old_settings = self._settings
            old_report = self._report
        new_report = self._build(candidate.analysis)
        return _AnalysisChange(
            self,
            old_settings,
            old_report,
            candidate.analysis,
            new_report,
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("analysis refresh worker is already running")
        self._stop.clear()
        self._thread = Thread(target=self._run, name="sigraft-analysis", daemon=True)
        self._thread.start()

    def refresh_once(self) -> bool:
        with self._lock:
            settings = self._settings
        try:
            report = self._build(settings)
        except AnalysisError:
            log.exception("operational analysis refresh failed")
            with self._lock:
                self._last_result = "failed"
                self._last_error = "analysis refresh failed"
            return False
        with self._lock:
            self._report = report
            self._last_result = "refreshed"
            self._last_error = None
        return True

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("analysis refresh worker did not stop")

    def _build(self, settings: AnalysisSettings) -> AnalysisReport | None:
        if not settings.enabled:
            return None
        return build_analysis_report(settings.observations_path, base_dir=self._base_dir)

    def _publish(
        self,
        settings: AnalysisSettings,
        report: AnalysisReport | None,
    ) -> None:
        with self._lock:
            self._settings = settings
            self._report = report
            self._last_result = "configured"
            self._last_error = None
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                interval = self._settings.refresh_interval_seconds
            self._wake.wait(interval)
            self._wake.clear()
            if not self._stop.is_set():
                self.refresh_once()


def _report_handler(runtime: AnalyticsRuntime) -> type[BaseHTTPRequestHandler]:
    class ReportHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._write(200, b'{"status":"ready"}', "application/json")
                return
            if self.path != "/":
                self._write(404, b'{"error":"not found"}', "application/json")
                return
            report = runtime.report
            if report is None:
                self._write(503, b'{"error":"analysis unavailable"}', "application/json")
                return
            self._write(200, report.html.encode(), "text/html; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def _write(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(payload)

    return ReportHandler


def _verify_release(candidate_digest: str, observations: str) -> int:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_digest) is None:
        raise SystemExit("candidate digest must be a sha256 digest")
    report = build_analysis_report(observations)
    baseline, candidate = report.summaries
    passed = (
        candidate.failure_rate <= baseline.failure_rate
        and candidate.p95_ms <= baseline.p95_ms * 1.10
    )
    print(
        json.dumps(
            {
                "candidate_digest": candidate_digest,
                "analysis_revision": report.revision,
                "passed": passed,
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve or gate SigRaft operational analysis")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve")
    serve.add_argument("--observations", default="package:observations.csv")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8082)
    verify = subcommands.add_parser("verify-release")
    verify.add_argument("--candidate-digest", required=True)
    verify.add_argument("--observations", default="package:observations.csv")
    arguments = parser.parse_args(argv)
    if arguments.command == "verify-release":
        return _verify_release(arguments.candidate_digest, arguments.observations)

    runtime = AnalyticsRuntime(
        AnalysisSettings(True, arguments.observations, 60.0),
        base_dir=Path.cwd(),
    )
    runtime.start()
    server = ThreadingHTTPServer((arguments.host, arguments.port), _report_handler(runtime))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
