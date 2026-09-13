"""Stable relay task contract for the stateless service checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    """Allowed lifecycle states for a relay task."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, order=True)
class TaskKey:
    """Stable task identity shared across the relay checkpoints."""

    tenant_id: str
    task_id: str

    def ring_key(self) -> str:
        """Return the canonical key format used in later checkpoints."""
        return f"{self.tenant_id}:{self.task_id}"


@dataclass(frozen=True)
class RelayTask:
    """Stored relay task record."""

    key: TaskKey
    title: str
    status: TaskStatus
    payload: Mapping[str, str] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    etag: str = ""


@dataclass(frozen=True)
class CreateTaskCommand:
    """Request to create a task."""

    key: TaskKey
    title: str
    status: TaskStatus = TaskStatus.QUEUED
    payload: Mapping[str, str] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatchTaskCommand:
    """Partial task update."""

    title: str | None = None
    status: TaskStatus | None = None
    payload: Mapping[str, str] | None = None
    depends_on: tuple[str, ...] | None = None
