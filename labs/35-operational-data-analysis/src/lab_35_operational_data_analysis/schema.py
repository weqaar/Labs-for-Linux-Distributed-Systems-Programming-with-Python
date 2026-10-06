"""Schema and validation for relay operational observations.

A row is one completed relay task attempt: a timestamp, its task and release,
the queue depth measured when it was submitted, how long it took, and how it
ended. A row that does not match the
documented schema is counted and its reason kept, never silently dropped or
silently repaired, so a report can state exactly how much of the input it
used.

The header must contain exactly the documented columns, so an unexpected
export format fails instead of silently losing fields. Length and character
limits on ``task_id``, ``release`` and ``region`` reject malformed labels
before creating an :class:`Observation`. The renderer still escapes values
for HTML and SVG; validation does not replace output escaping.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_COLUMNS = (
    "timestamp",
    "task_id",
    "release",
    "region",
    "queue_depth_at_submit",
    "duration_ms",
    "outcome",
    "attempt",
)

VALID_OUTCOMES = frozenset({"succeeded", "failed"})

# A safe token: starts with a letter or digit, then any run of letters,
# digits, dots, hyphens or underscores. This covers every value the fixture
# actually uses (task_id like "task-2024050-0001", release like "2024.05.0",
# region like "eastus") while excluding markup delimiters, quotes, whitespace
# and control characters that would otherwise reach an HTML or SVG renderer.
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

TASK_ID_MAX_LENGTH = 64
RELEASE_MAX_LENGTH = 32
REGION_MAX_LENGTH = 32


@dataclass(frozen=True)
class Observation:
    """One completed relay task attempt, one row of tidy input data."""

    timestamp: datetime
    task_id: str
    release: str
    region: str
    queue_depth_at_submit: int
    duration_ms: float
    outcome: str
    attempt: int

    @property
    def failed(self) -> bool:
        return self.outcome == "failed"


@dataclass(frozen=True)
class RejectedRow:
    """A row that failed validation, kept so nothing is silently dropped."""

    line_number: int
    reason: str
    raw: dict[str, str]


@dataclass(frozen=True)
class LoadReport:
    """The outcome of loading and validating one CSV file."""

    observations: tuple[Observation, ...]
    rejected: tuple[RejectedRow, ...]
    duplicate_task_ids: int
    source: Path

    @property
    def valid_count(self) -> int:
        return len(self.observations)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def _validate_safe_token(value: str, field: str, max_length: int) -> str:
    """Bound *value* to a safe character set and length before it is kept.

    ``task_id``, ``release`` and ``region`` end up in an HTML page and an
    SVG chart. This rejects a value outright rather than truncating or
    stripping it, so a report never has to guess what an operator meant by
    an oversized or malformed field.
    """
    if len(value) > max_length:
        raise ValueError(f"{field} is longer than {max_length} characters")
    if not _SAFE_TOKEN_RE.match(value):
        raise ValueError(f"{field} contains characters outside the allowed pattern")
    return value


def _parse_utc_timestamp(raw: str) -> datetime:
    """Parse a UTC timestamp, requiring the schema's trailing ``Z``.

    A timestamp without a zone does not identify an unambiguous instant.
    Reject it rather than guessing the server's local timezone.
    """
    if not raw.endswith("Z"):
        raise ValueError("timestamp must be UTC and end with 'Z'")
    parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    return parsed.astimezone(timezone.utc)


def _validate_row(raw: dict[str, str]) -> Observation:
    """Validate one CSV row against the documented schema.

    Raises ``ValueError`` describing the first violation found. Callers keep
    the row and the message rather than catching a broad exception, so a
    validation report always names a specific reason.
    """
    missing = [column for column in REQUIRED_COLUMNS if not raw.get(column, "").strip()]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")

    timestamp = _parse_utc_timestamp(raw["timestamp"].strip())

    try:
        queue_depth = int(raw["queue_depth_at_submit"])
    except ValueError as exc:
        raise ValueError("queue_depth_at_submit is not an integer") from exc
    if queue_depth < 0:
        raise ValueError("queue_depth_at_submit cannot be negative")

    try:
        duration_ms = float(raw["duration_ms"])
    except ValueError as exc:
        raise ValueError("duration_ms is not a number") from exc
    # float() parses "nan", "inf" and "-inf" without raising, so a non-finite
    # value has to be rejected explicitly; left unchecked it would slip past
    # the positivity test below (neither NaN nor infinity is <= 0) and reach
    # NumPy's mean, poisoning every downstream summary silently.
    if not math.isfinite(duration_ms):
        raise ValueError("duration_ms must be a finite number, not NaN or infinite")
    if duration_ms <= 0:
        raise ValueError("duration_ms must be a positive number of milliseconds")

    outcome = raw["outcome"].strip()
    if outcome not in VALID_OUTCOMES:
        allowed = ", ".join(sorted(VALID_OUTCOMES))
        raise ValueError(f"outcome '{outcome}' is not one of: {allowed}")

    try:
        attempt = int(raw["attempt"])
    except ValueError as exc:
        raise ValueError("attempt is not an integer") from exc
    if attempt < 1:
        raise ValueError("attempt must be 1 or greater")

    task_id = _validate_safe_token(raw["task_id"].strip(), "task_id", TASK_ID_MAX_LENGTH)
    release = _validate_safe_token(raw["release"].strip(), "release", RELEASE_MAX_LENGTH)
    region = _validate_safe_token(raw["region"].strip(), "region", REGION_MAX_LENGTH)

    return Observation(
        timestamp=timestamp,
        task_id=task_id,
        release=release,
        region=region,
        queue_depth_at_submit=queue_depth,
        duration_ms=duration_ms,
        outcome=outcome,
        attempt=attempt,
    )


def load_observations(path: Path) -> LoadReport:
    """Load and validate *path* against the documented schema.

    Every row is checked. A row that fails validation is recorded in
    ``rejected`` with its reason instead of being dropped or coerced. A
    repeated task identifier is counted, not rejected. Retried attempts remain
    in the data and affect attempt-level averages; the duplicate count does not
    make those observations statistically independent.
    """
    observations: list[Observation] = []
    rejected: list[RejectedRow] = []
    seen_task_ids: set[str] = set()
    duplicates = 0

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        # Reject unexpected columns instead of silently discarding export fields.
        if fieldnames is None or set(fieldnames) != set(REQUIRED_COLUMNS):
            raise ValueError(f"{path} does not have the documented columns: {REQUIRED_COLUMNS}")
        for line_number, raw in enumerate(reader, start=2):
            try:
                observation = _validate_row(raw)
            except ValueError as exc:
                rejected.append(RejectedRow(line_number=line_number, reason=str(exc), raw=raw))
                continue
            if observation.task_id in seen_task_ids:
                duplicates += 1
            seen_task_ids.add(observation.task_id)
            observations.append(observation)

    return LoadReport(
        observations=tuple(observations),
        rejected=tuple(rejected),
        duplicate_task_ids=duplicates,
        source=path,
    )


def default_dataset_path() -> Path:
    """Return the bundled fixture path used when no path is supplied."""
    return Path(__file__).parent / "data" / "observations.csv"
