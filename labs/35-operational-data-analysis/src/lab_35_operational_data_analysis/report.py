"""Rendering: a deterministic accessible SVG chart and a safe HTML report.

Chapter 35's rule is that a chart never travels without the numbers it was
drawn from. This module keeps that rule structurally: the HTML page always
prints the descriptive statistics, the confidence interval, the comparison
and the regression as text and tables, and the chart is one more view of the
same numbers rather than a replacement for them.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from html import escape

import matplotlib

# The Agg backend renders to a memory buffer without a display, which is what
# a server process needs. This must run before pyplot is imported anywhere in
# the process, so it happens at import time of this module rather than inside
# a function.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from lab_35_operational_data_analysis.analysis import (
    ComparisonResult,
    ConfidenceInterval,
    RegressionResult,
    ReleaseSummary,
)
from lab_35_operational_data_analysis.schema import RejectedRow

# A colour-blind-safe pair (Okabe-Ito blue and vermillion). Line style and
# marker also differ between the two series, so the chart does not rely on
# colour alone to distinguish them.
SUCCESS_COLOR = "#0072B2"
FAILURE_COLOR = "#D55E00"

# Fixes the identifiers matplotlib's SVG backend otherwise draws from a
# random salt, so the same input renders the same bytes on every run.
_SVG_HASH_SALT = "sigraft-lab-35-report"

_SVG_OPEN_TAG_RE = re.compile(r"<svg\b[^>]*>")


@dataclass(frozen=True)
class Environment:
    """Library versions recorded for reproducibility."""

    numpy: str
    pandas: str
    matplotlib: str
    scipy: str
    statsmodels: str


def current_environment() -> Environment:
    """Read the versions of the libraries this analysis depends on."""
    import scipy
    import statsmodels

    return Environment(
        numpy=np.__version__,
        pandas=pd.__version__,
        matplotlib=matplotlib.__version__,
        scipy=scipy.__version__,
        statsmodels=statsmodels.__version__,
    )


def _add_accessibility(svg_text: str, title: str, description: str) -> str:
    """Insert a ``role``, a ``<title>`` and a ``<desc>`` into an SVG document.

    Screen readers announce the title and description of an image with a
    role; an SVG chart with neither is decorative to assistive technology
    even when it carries the entire finding.
    """
    match = _SVG_OPEN_TAG_RE.search(svg_text)
    if match is None:
        raise ValueError("rendered output does not contain an <svg> element")
    open_tag = match.group(0)
    accessible_tag = open_tag[:-1] + ' role="img">'
    accessible_block = f"<title>{escape(title)}</title><desc>{escape(description)}</desc>"
    return svg_text[: match.start()] + accessible_tag + accessible_block + svg_text[match.end() :]


def render_svg_report(
    frame: pd.DataFrame,
    summaries: tuple[ReleaseSummary, ...],
) -> str:
    """Render a two-panel accessible SVG: a time series and a percentile view.

    Both panels carry axis labels with units, because an unlabelled duration
    axis invites the reader to guess the unit rather than read it.
    """
    previous_salt = matplotlib.rcParams.get("svg.hashsalt")
    matplotlib.rcParams["svg.hashsalt"] = _SVG_HASH_SALT
    try:
        fig, (series_ax, percentile_ax) = plt.subplots(2, 1, figsize=(8.0, 7.0))

        for outcome, color, marker in (
            ("succeeded", SUCCESS_COLOR, "o"),
            ("failed", FAILURE_COLOR, "x"),
        ):
            rows = frame.loc[frame["outcome"] == outcome]
            series_ax.scatter(
                rows["timestamp"],
                rows["duration_ms"],
                s=14,
                c=color,
                marker=marker,
                label=outcome,
                alpha=0.8,
            )
        series_ax.set_xlabel("Time (UTC)")
        series_ax.set_ylabel("Task duration (ms)")
        series_ax.set_title("relay task duration over time, by outcome")
        series_ax.legend(loc="upper left")
        fig.autofmt_xdate()

        linestyles = ("-", "--")
        for summary, linestyle in zip(summaries, linestyles, strict=False):
            durations = np.sort(
                frame.loc[frame["release"] == summary.release, "duration_ms"].to_numpy()
            )
            cumulative = np.arange(1, durations.size + 1) / durations.size
            percentile_ax.plot(
                durations,
                cumulative,
                linestyle=linestyle,
                color=SUCCESS_COLOR if linestyle == "-" else FAILURE_COLOR,
                label=f"{summary.release} (n={summary.count})",
            )
        percentile_ax.axhline(0.95, color="black", linewidth=0.6, linestyle=":")
        percentile_ax.set_xlabel("Task duration (ms)")
        percentile_ax.set_ylabel("Cumulative proportion of tasks")
        percentile_ax.set_title("Empirical cumulative distribution by release")
        percentile_ax.legend(loc="lower right")

        fig.tight_layout()
        buffer = io.StringIO()
        # Date metadata is suppressed so that the same input renders to the
        # same bytes on every run, which the deterministic-output tests and
        # the reproducibility guidance in Chapter 35 both rely on.
        fig.savefig(buffer, format="svg", metadata={"Date": None})
        svg_text = buffer.getvalue()
    finally:
        plt.close("all")
        matplotlib.rcParams["svg.hashsalt"] = previous_salt

    description = (
        "Two charts: relay task duration in milliseconds over time coloured by "
        "outcome, and the empirical cumulative distribution of duration for "
        "each release with a dotted line at the 95th percentile."
    )
    return _add_accessibility(
        svg_text, title="relay operational duration report", description=description
    )


def _release_rows(summaries: tuple[ReleaseSummary, ...]) -> str:
    rows = []
    for summary in summaries:
        rows.append(
            "<tr>"
            f"<td>{escape(summary.release)}</td>"
            f"<td>{summary.count}</td>"
            f"<td>{summary.failures}</td>"
            f"<td>{summary.failure_rate:.2%}</td>"
            f"<td>{summary.mean_ms:.1f}</td>"
            f"<td>{summary.median_ms:.1f}</td>"
            f"<td>{summary.p95_ms:.1f}</td>"
            f"<td>{summary.p99_ms:.1f}</td>"
            f"<td>{summary.sample_std_ms:.1f}</td>"
            "</tr>"
        )
    return "".join(rows)


def _rejected_rows(rejected: tuple[RejectedRow, ...], limit: int = 10) -> str:
    if not rejected:
        return "<p>No rows were rejected.</p>"
    items = "".join(
        f"<li>line {row.line_number}: {escape(row.reason)}</li>" for row in rejected[:limit]
    )
    remainder = len(rejected) - min(limit, len(rejected))
    footer = f"<p>...and {remainder} more.</p>" if remainder > 0 else ""
    return f"<ul>{items}</ul>{footer}"


def render_html_report(
    *,
    source_label: str,
    generated_at_label: str,
    valid_count: int,
    rejected: tuple[RejectedRow, ...],
    duplicate_task_ids: int,
    summaries: tuple[ReleaseSummary, ...],
    confidence: ConfidenceInterval,
    comparison: ComparisonResult,
    regression: RegressionResult,
    svg: str,
    environment: Environment,
) -> str:
    """Build the analysis web page as a self-contained HTML document.

    Every dynamic value that becomes HTML text or attribute content is
    escaped, including the dataset path and the rejection reasons, which
    come from the CSV file rather than from a literal in this module.
    """
    release_coefficient_rows = "".join(
        f"<li>{escape(release)}: {value:+.1f} ms relative to the baseline release</li>"
        for release, value in regression.release_coefficients_ms.items()
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SigRaft relay operational data analysis</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; color: #1b1b1b; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
th, td {{ border: 1px solid #888; padding: 0.3rem 0.6rem; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
caption {{ text-align: left; font-weight: bold; margin-bottom: 0.3rem; }}
.caveat {{ color: #6b4a00; }}
</style>
</head>
<body>
<h1>SigRaft relay operational data analysis</h1>
<p>Source: <code>{escape(source_label)}</code>. Generated: {escape(generated_at_label)}.</p>
<p>Valid observations: {valid_count}. Rejected rows: {len(rejected)}.
Duplicate task identifiers (retried tasks counted more than once): {duplicate_task_ids}.</p>

<h2>Per-release summary</h2>
<table>
<caption>Duration in milliseconds unless noted</caption>
<thead>
<tr><th>Release</th><th>Count</th><th>Failures</th><th>Failure rate</th>
<th>Mean</th><th>Median</th><th>p95</th><th>p99</th><th>Sample std (ddof=1)</th></tr>
</thead>
<tbody>{_release_rows(summaries)}</tbody>
</table>

<h2>Confidence interval for overall mean duration</h2>
<p>Mean {confidence.mean_ms:.1f} ms, {confidence.confidence:.0%} confidence interval
[{confidence.lower_ms:.1f}, {confidence.upper_ms:.1f}] ms, n={confidence.sample_size}.
This interval describes uncertainty in the estimated mean; it is not a
percentile and it does not bound any single task.</p>

<h2>Release comparison</h2>
<p>{escape(comparison.candidate_release)} (n={comparison.candidate_n}) versus
{escape(comparison.baseline_release)} (n={comparison.baseline_n}): Mann-Whitney
U={comparison.u_statistic:.1f}, p={comparison.p_value:.4f}, rank-biserial effect
size={comparison.rank_biserial:+.3f}.</p>
<p class="caveat">A p-value states how surprising this split would be if the two
releases had the same duration distribution. It does not state how much the
change matters operationally; the effect size and the release's latency
objective decide that.</p>

<h2>Duration model</h2>
<p>duration_ms ~ queue_depth_at_submit + release. Intercept
{regression.intercept_ms:.1f} ms. Queue depth coefficient
{regression.queue_depth_coefficient_ms:+.2f} ms per queued task.
R-squared {regression.r_squared:.3f}. Durbin-Watson {regression.durbin_watson:.2f}.
Residual standard deviation {regression.residual_std_ms:.1f} ms.</p>
<ul>{release_coefficient_rows}</ul>
<p class="caveat">Adding region to the same regression changes the queue depth
coefficient to {regression.queue_depth_coefficient_with_region_control_ms:+.2f} ms
per queued task. Region was correlated with both queue depth and duration in
this sample, so leaving it out changed the apparent relationship; this is a
reminder that a coefficient here is an association within these variables,
not a causal effect, and a missing confounder can even flip its sign.
Durbin-Watson away from 2 would mean the residuals are still correlated in
time order, which plain regression standard errors assume they are not.</p>

<h2>Rejected rows</h2>
{_rejected_rows(rejected)}

<h2>Chart</h2>
{svg}

<h2>Reproducibility</h2>
<p>numpy {escape(environment.numpy)}, pandas {escape(environment.pandas)},
matplotlib {escape(environment.matplotlib)}, scipy {escape(environment.scipy)},
statsmodels {escape(environment.statsmodels)}.</p>
</body>
</html>
"""
