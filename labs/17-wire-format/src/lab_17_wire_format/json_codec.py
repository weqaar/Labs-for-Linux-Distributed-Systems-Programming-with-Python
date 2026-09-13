"""A JSON patch decoder that preserves field presence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

MAX_MESSAGE_BYTES = 1024 * 1024


class OwnerOperation(Enum):
    """Meaning of the owner field in a patch."""

    UNCHANGED = "unchanged"
    CLEAR = "clear"
    SET = "set"


@dataclass(frozen=True)
class OwnerPatch:
    """Validated owner update."""

    operation: OwnerOperation
    value: str | None = None


def decode_owner_patch(body: bytes, *, maximum: int = MAX_MESSAGE_BYTES) -> OwnerPatch:
    """Decode an owner patch without collapsing absent and null."""
    if len(body) > maximum:
        raise ValueError(f"message exceeds {maximum} bytes")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON message must be an object")
    unknown = payload.keys() - {"owner"}
    if unknown:
        raise ValueError(f"unknown field: {sorted(unknown)[0]}")
    if "owner" not in payload:
        return OwnerPatch(OwnerOperation.UNCHANGED)
    if payload["owner"] is None:
        return OwnerPatch(OwnerOperation.CLEAR)
    if not isinstance(payload["owner"], str) or not payload["owner"].strip():
        raise ValueError("owner must be a non-empty string or null")
    return OwnerPatch(OwnerOperation.SET, payload["owner"])
