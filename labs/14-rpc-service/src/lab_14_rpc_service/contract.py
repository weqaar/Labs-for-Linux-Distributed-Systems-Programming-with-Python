"""Shared relay task contract for the RPC checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

TASKS_COLLECTION_PATH = "/tasks"


class ContractError(ValueError):
    """Raised when a task payload breaks the shared relay contract."""


class TaskAction(str, Enum):
    """Supported relay actions."""

    INDEX = "index"
    DELIVER = "deliver"
    ARCHIVE = "archive"


class TaskState(str, Enum):
    """Task states shared by every relay checkpoint."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskSubmission:
    """HTTP POST /tasks body."""

    id: str
    action: TaskAction
    target: str
    submitted_at: datetime

    def __post_init__(self) -> None:
        _require_task_id(self.id)
        _require_text(self.target, field_name="target")
        object.__setattr__(self, "submitted_at", ensure_utc(self.submitted_at))

    def to_mapping(self) -> dict[str, str]:
        return {
            "id": self.id,
            "action": self.action.value,
            "target": self.target,
            "submitted_at": format_timestamp(self.submitted_at),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TaskSubmission:
        _require_keys(payload, {"id", "action", "target", "submitted_at"})
        try:
            action = TaskAction(_require_string(payload.get("action"), field_name="action"))
        except ValueError as exc:
            raise ContractError(f"unknown action: {payload['action']}") from exc
        return cls(
            id=_require_string(payload.get("id"), field_name="id"),
            action=action,
            target=_require_string(payload.get("target"), field_name="target"),
            submitted_at=parse_timestamp(payload.get("submitted_at"), field_name="submitted_at"),
        )


@dataclass(frozen=True)
class TaskStatus:
    """HTTP GET /tasks/{id} response body."""

    id: str
    action: TaskAction
    target: str
    state: TaskState
    submitted_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_task_id(self.id)
        _require_text(self.target, field_name="target")
        submitted_at = ensure_utc(self.submitted_at)
        updated_at = ensure_utc(self.updated_at)
        if updated_at < submitted_at:
            raise ContractError("updated_at must be on or after submitted_at")
        object.__setattr__(self, "submitted_at", submitted_at)
        object.__setattr__(self, "updated_at", updated_at)

    def to_mapping(self) -> dict[str, str]:
        return {
            "id": self.id,
            "action": self.action.value,
            "target": self.target,
            "state": self.state.value,
            "submitted_at": format_timestamp(self.submitted_at),
            "updated_at": format_timestamp(self.updated_at),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TaskStatus:
        _require_keys(
            payload,
            {"id", "action", "target", "state", "submitted_at", "updated_at"},
        )
        try:
            action = TaskAction(_require_string(payload.get("action"), field_name="action"))
            state = TaskState(_require_string(payload.get("state"), field_name="state"))
        except ValueError as exc:
            raise ContractError("unknown task action or state") from exc
        return cls(
            id=_require_string(payload.get("id"), field_name="id"),
            action=action,
            target=_require_string(payload.get("target"), field_name="target"),
            state=state,
            submitted_at=parse_timestamp(payload.get("submitted_at"), field_name="submitted_at"),
            updated_at=parse_timestamp(payload.get("updated_at"), field_name="updated_at"),
        )


def ensure_utc(value: datetime) -> datetime:
    """Reject naive or non-UTC timestamps and normalise zero offsets."""

    offset = value.utcoffset()
    if offset is None:
        raise ContractError("timestamp must be timezone aware")
    if offset != timedelta(0):
        raise ContractError("timestamp must be in UTC")
    return value.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """Render one UTC timestamp for the relay JSON contract."""

    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any, *, field_name: str) -> datetime:
    """Parse one RFC3339 UTC timestamp from a JSON payload."""

    text = _require_string(value, field_name=field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field_name} must be an RFC3339 timestamp") from exc
    return ensure_utc(parsed)


def _require_task_id(value: str) -> None:
    if not value.startswith("task-") or not value[5:].isdigit() or value[5:].startswith("0"):
        raise ContractError("id must match task-<positive integer>")


def _require_keys(payload: Mapping[str, Any], expected: set[str]) -> None:
    missing = sorted(expected - payload.keys())
    extra = sorted(payload.keys() - expected)
    if missing:
        listed = ", ".join(missing)
        raise ContractError(f"missing fields: {listed}")
    if extra:
        listed = ", ".join(extra)
        raise ContractError(f"unknown fields: {listed}")


def _require_text(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ContractError(f"{field_name} must not be empty")


def _require_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field_name} must be a string")
    _require_text(value, field_name=field_name)
    return value
