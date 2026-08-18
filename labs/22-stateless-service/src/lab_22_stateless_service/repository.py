"""Repository abstraction and in-memory implementation for relay tasks."""

from __future__ import annotations

from dataclasses import replace
from itertools import count
from threading import Lock
from typing import Protocol

from lab_22_stateless_service.contract import (
    CreateTaskCommand,
    PatchTaskCommand,
    RelayTask,
    TaskKey,
)


class TaskRepositoryError(Exception):
    """Base class for repository failures."""


class TaskAlreadyExistsError(TaskRepositoryError):
    """Raised when creating a duplicate task."""


class TaskNotFoundError(TaskRepositoryError):
    """Raised when a task is missing."""


class RepositoryUnavailableError(TaskRepositoryError):
    """Raised when the backing repository cannot serve requests."""


class ConcurrencyConflictError(TaskRepositoryError):
    """Raised when an ETag precondition fails."""


class TaskRepository(Protocol):
    """Storage contract injected into the HTTP application."""

    def create_task(self, command: CreateTaskCommand) -> RelayTask:
        """Persist a new task."""
        ...

    def get_task(self, key: TaskKey) -> RelayTask:
        """Fetch one task by key."""
        ...

    def list_tasks(self, tenant_id: str | None = None) -> list[RelayTask]:
        """List tasks in deterministic order."""
        ...

    def update_task(
        self,
        key: TaskKey,
        patch: PatchTaskCommand,
        *,
        if_match: str,
    ) -> RelayTask:
        """Apply a conditional update."""
        ...

    def is_ready(self) -> bool:
        """Report whether the dependency is ready."""
        ...


class InMemoryTaskRepository:
    """Thread-safe fake repository shared by multiple app instances in tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._ready = True
        self._etag_counter = count(1)
        self._tasks: dict[TaskKey, RelayTask] = {}

    def set_ready(self, ready: bool) -> None:
        """Change readiness without recreating the repository."""
        with self._lock:
            self._ready = ready

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def create_task(self, command: CreateTaskCommand) -> RelayTask:
        with self._lock:
            self._ensure_ready()
            if command.key in self._tasks:
                raise TaskAlreadyExistsError(command.key.ring_key())
            task = RelayTask(
                key=command.key,
                title=command.title,
                status=command.status,
                payload=dict(command.payload),
                depends_on=tuple(command.depends_on),
                etag=self._next_etag(),
            )
            self._tasks[command.key] = task
            return task

    def get_task(self, key: TaskKey) -> RelayTask:
        with self._lock:
            self._ensure_ready()
            return self._stored_task(key)

    def list_tasks(self, tenant_id: str | None = None) -> list[RelayTask]:
        with self._lock:
            self._ensure_ready()
            tasks = [
                task
                for task in self._tasks.values()
                if tenant_id is None or task.key.tenant_id == tenant_id
            ]
            return sorted(tasks, key=lambda task: (task.key.tenant_id, task.key.task_id))

    def update_task(
        self,
        key: TaskKey,
        patch: PatchTaskCommand,
        *,
        if_match: str,
    ) -> RelayTask:
        with self._lock:
            self._ensure_ready()
            current = self._stored_task(key)
            if current.etag != if_match:
                raise ConcurrencyConflictError(key.ring_key())
            updated = replace(
                current,
                title=current.title if patch.title is None else patch.title,
                status=current.status if patch.status is None else patch.status,
                payload=current.payload if patch.payload is None else dict(patch.payload),
                depends_on=(
                    current.depends_on if patch.depends_on is None else tuple(patch.depends_on)
                ),
                etag=self._next_etag(),
            )
            self._tasks[key] = updated
            return updated

    def _ensure_ready(self) -> None:
        if not self._ready:
            raise RepositoryUnavailableError("repository unavailable")

    def _stored_task(self, key: TaskKey) -> RelayTask:
        task = self._tasks.get(key)
        if task is None:
            raise TaskNotFoundError(key.ring_key())
        return task

    def _next_etag(self) -> str:
        return f"{next(self._etag_counter):08d}"
