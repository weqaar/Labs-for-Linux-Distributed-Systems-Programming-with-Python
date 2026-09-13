"""Repository interface and deterministic local adapter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .models import Task


@runtime_checkable
class TaskRepository(Protocol):
    """Structural interface implemented by local and cloud adapters."""

    def add(self, task: Task) -> None: ...

    def get(self, task_id: str) -> Task: ...

    def replace(self, task: Task) -> None: ...

    def list(self) -> tuple[Task, ...]: ...


class InMemoryTaskRepository:
    """Encapsulated dictionary adapter for deterministic tests."""

    def __init__(self, tasks: Iterable[Task] = ()) -> None:
        self._tasks: dict[str, Task] = {}
        for task in tasks:
            self.add(task)

    def add(self, task: Task) -> None:
        if task.id in self._tasks:
            raise ValueError(f"task already exists: {task.id}")
        self._tasks[task.id] = task

    def get(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise LookupError(f"task not found: {task_id}") from exc

    def replace(self, task: Task) -> None:
        if task.id not in self._tasks:
            raise LookupError(f"task not found: {task.id}")
        self._tasks[task.id] = task

    def list(self) -> tuple[Task, ...]:
        return tuple(self._tasks.values())
