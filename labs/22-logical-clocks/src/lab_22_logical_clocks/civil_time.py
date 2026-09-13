"""Civil-time parsing and presentation for relay task records."""

from __future__ import annotations

import re
from datetime import datetime

import pendulum

from lab_22_logical_clocks.relay import ContractError, ensure_utc

_EXPLICIT_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")


def parse_utc_timestamp(value: str) -> datetime:
    """Parse an explicit-offset timestamp and normalize it to UTC."""

    if _EXPLICIT_OFFSET.search(value) is None:
        raise ContractError("timestamp must include Z or an explicit UTC offset")
    try:
        parsed = pendulum.parse(value, strict=True)
    except (TypeError, ValueError) as exc:
        raise ContractError("timestamp must be valid ISO 8601") from exc
    if not isinstance(parsed, pendulum.DateTime):
        raise ContractError("timestamp must include a date and time")
    return parsed.in_timezone("UTC")


def display_in_timezone(value: datetime, timezone_name: str) -> str:
    """Render a UTC task timestamp in an operator's IANA timezone."""

    normalized = ensure_utc(value)
    try:
        return pendulum.instance(normalized).in_timezone(timezone_name).to_iso8601_string()
    except (TypeError, ValueError) as exc:
        raise ContractError(f"unknown timezone: {timezone_name}") from exc
