"""Relay domain objects and validation descriptors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import overload


class TaskAction(str, Enum):
    """Actions supported by the checkpoint."""

    INDEX = "index"
    ARCHIVE = "archive"


class TaskState(str, Enum):
    """States shared by every relay checkpoint."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Task:
    """Immutable task value crossing repository and handler boundaries."""

    id: str
    action: TaskAction
    payload: str
    state: TaskState = TaskState.QUEUED

    def __post_init__(self) -> None:
        if not self.id.startswith("task-") or not self.id[5:].isdigit():
            raise ValueError("id must match task-<integer>")
        if not self.payload.strip():
            raise ValueError("payload must not be empty")

    @property
    def is_terminal(self) -> bool:
        """Return whether no later state is expected."""

        return self.state in {TaskState.SUCCEEDED, TaskState.FAILED}

    def with_state(self, state: TaskState) -> Task:
        """Return a new value rather than exposing mutable state."""

        return replace(self, state=state)


class PositiveInteger:
    """Data descriptor enforcing positive integer configuration."""

    def __set_name__(self, owner: type[object], name: str) -> None:
        self._storage_name = f"_{name}"

    @overload
    def __get__(self, instance: None, owner: type[object]) -> PositiveInteger: ...

    @overload
    def __get__(self, instance: object, owner: type[object]) -> int: ...

    def __get__(self, instance: object | None, owner: type[object]) -> PositiveInteger | int:
        if instance is None:
            return self
        return int(getattr(instance, self._storage_name))

    def __set__(self, instance: object, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("value must be a positive integer")
        object.__setattr__(instance, self._storage_name, value)


class WorkerPolicy:
    """Slot-backed worker policy using a reusable validation descriptor."""

    __slots__ = ("_max_attempts",)

    max_attempts = PositiveInteger()

    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts
