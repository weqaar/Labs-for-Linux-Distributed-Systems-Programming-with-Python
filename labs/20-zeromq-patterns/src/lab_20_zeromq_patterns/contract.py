"""Shared relay work contract for the ZeroMQ checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


class ContractError(ValueError):
    """Raised when a relay task or event breaks the shared contract."""


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
    """One relay task handed into the PUSH/PULL pipeline."""

    id: str
    action: TaskAction
    target: str
    submitted_at: datetime

    def __post_init__(self) -> None:
        _require_task_id(self.id)
        _require_text(self.target, field_name="target")
        object.__setattr__(self, "submitted_at", ensure_utc(self.submitted_at))


@dataclass(frozen=True)
class TaskStatusEvent:
    """Status event distributed over PUB/SUB."""

    sequence: int
    id: str
    action: TaskAction
    state: TaskState
    detail: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ContractError("sequence must be at least one")
        _require_task_id(self.id)
        _require_text(self.detail, field_name="detail")


@dataclass(frozen=True)
class CommandRequest:
    """Asynchronous command sent over a DEALER/ROUTER channel."""

    client_id: str
    request_id: str
    task_id: str
    command: str

    def __post_init__(self) -> None:
        _require_text(self.client_id, field_name="client_id")
        _require_text(self.request_id, field_name="request_id")
        _require_task_id(self.task_id)
        _require_text(self.command, field_name="command")


@dataclass(frozen=True)
class CommandReply:
    """Reply matched to the request ID that produced it."""

    client_id: str
    request_id: str
    task_id: str
    accepted: bool
    message: str

    def __post_init__(self) -> None:
        _require_text(self.client_id, field_name="client_id")
        _require_text(self.request_id, field_name="request_id")
        _require_task_id(self.task_id)
        _require_text(self.message, field_name="message")


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


def _require_text(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ContractError(f"{field_name} must not be empty")
