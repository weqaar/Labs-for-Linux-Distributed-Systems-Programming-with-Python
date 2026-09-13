"""Relay task models used by the packet-tools checkpoint."""

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
    """A relay task under diagnosis."""

    task_id: str
    definition: str
    state: TaskState = TaskState.RUNNING

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.definition.strip():
            raise ValueError("definition must not be empty")
