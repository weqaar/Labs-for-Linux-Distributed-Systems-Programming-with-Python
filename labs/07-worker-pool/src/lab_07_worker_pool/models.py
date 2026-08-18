"""Relay task models shared by the worker-pool checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskState(str, Enum):
    """Lifecycle states shared by the relay checkpoints."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RelayTask:
    """A relay task definition ready for the queue."""

    task_id: str
    definition: str
    state: TaskState = TaskState.QUEUED

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.definition.strip():
            raise ValueError("definition must not be empty")


@dataclass(frozen=True)
class RelayTaskSnapshot:
    """Observed state for a task being processed by the worker pool."""

    task_id: str
    definition: str
    state: TaskState
    deliveries: int = 0
