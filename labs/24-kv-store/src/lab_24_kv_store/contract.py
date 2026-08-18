"""Stable relay task contract for the key-value store checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    """Allowed lifecycle states for relay tasks."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, order=True)
class TaskKey:
    """Stable task identity shared across relay checkpoints."""

    tenant_id: str
    task_id: str


@dataclass(frozen=True)
class RelayTaskRecord:
    """Task body stored in the fake Cosmos-like container."""

    key: TaskKey
    title: str
    status: TaskStatus = TaskStatus.QUEUED
    payload: Mapping[str, str] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskPatch:
    """Partial relay task update."""

    title: str | None = None
    status: TaskStatus | None = None
    payload: Mapping[str, str] | None = None
    depends_on: tuple[str, ...] | None = None
