"""Stable relay task contract for the partitioned store checkpoint."""

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

    def ring_key(self) -> str:
        """Return the canonical hashing key for the ring."""
        return f"{self.tenant_id}:{self.task_id}"


@dataclass(frozen=True)
class RelayTaskRecord:
    """Task payload stored on the replicated ring."""

    key: TaskKey
    title: str
    status: TaskStatus = TaskStatus.QUEUED
    payload: Mapping[str, str] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
