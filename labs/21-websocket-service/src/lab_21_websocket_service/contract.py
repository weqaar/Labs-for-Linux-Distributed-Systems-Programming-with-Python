"""Shared relay event contract for the WebSocket checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


class ContractError(ValueError):
    """Raised when a relay event or token breaks the shared contract."""


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
class BearerToken:
    """Upgrade token used by the fake WebSocket session."""

    subject: str
    expires_at_ms: int

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ContractError("subject must not be empty")
        if self.expires_at_ms < 0:
            raise ContractError("expires_at_ms must not be negative")


@dataclass(frozen=True)
class TaskEvent:
    """Status event streamed over the WebSocket channel."""

    sequence: int
    id: str
    action: TaskAction
    state: TaskState
    detail: str
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ContractError("sequence must be at least one")
        _require_task_id(self.id)
        if not self.detail.strip():
            raise ContractError("detail must not be empty")
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))

    def to_frame(self) -> dict[str, object]:
        return {
            "type": "event",
            "sequence": self.sequence,
            "id": self.id,
            "action": self.action.value,
            "state": self.state.value,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
        }


def ensure_utc(value: datetime) -> datetime:
    """Reject naive or non-UTC timestamps and normalise zero offsets."""

    offset = value.utcoffset()
    if offset is None:
        raise ContractError("timestamp must be timezone aware")
    if offset != timedelta(0):
        raise ContractError("timestamp must be in UTC")
    return value.astimezone(timezone.utc)


def _require_task_id(value: str) -> None:
    if not value.startswith("task-") or not value[5:].isdigit() or value[5:].startswith("0"):
        raise ContractError("id must match task-<positive integer>")
