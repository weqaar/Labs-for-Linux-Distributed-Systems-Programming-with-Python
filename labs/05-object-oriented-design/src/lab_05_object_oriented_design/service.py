"""Relay application service composed from typed object boundaries."""

from __future__ import annotations

import inspect
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Concatenate, ParamSpec, Protocol, TypeVar

from .handlers import Handler, HandlerMeta
from .models import Task, TaskAction, TaskState, WorkerPolicy
from .repository import InMemoryTaskRepository, TaskRepository

P = ParamSpec("P")
R = TypeVar("R")
S = TypeVar("S", bound="AuditSink")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One application-level method result."""

    operation: str
    task_id: str


class AuditSink(Protocol):
    """Object accepted by the method decorator."""

    def record_audit(self, operation: str, result: object) -> None: ...


def audited(
    method: Callable[Concatenate[S, P], R],
) -> Callable[Concatenate[S, P], R]:
    """Decorate an application method without changing its signature metadata."""

    @wraps(method)
    def wrapper(self: S, *args: P.args, **kwargs: P.kwargs) -> R:
        result = method(self, *args, **kwargs)
        self.record_audit(method.__name__, result)
        return result

    return wrapper


class TaskService:
    """Application service depending on interfaces rather than SDK classes."""

    def __init__(
        self,
        repository: TaskRepository,
        handlers: dict[TaskAction, Handler],
        *,
        policy: WorkerPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._handlers = dict(handlers)
        self._policy = policy or WorkerPolicy()
        self._audit: list[AuditEvent] = []

    @audited
    def submit(self, task: Task) -> Task:
        self._repository.add(task)
        return task

    @audited
    def run(self, task_id: str) -> Task:
        task = self._repository.get(task_id)
        running = task.with_state(TaskState.RUNNING)
        self._repository.replace(running)
        handler = self._handlers[running.action]
        handler.execute(running)
        completed = running.with_state(TaskState.SUCCEEDED)
        self._repository.replace(completed)
        return completed

    def record_audit(self, operation: str, result: object) -> None:
        if isinstance(result, Task):
            self._audit.append(AuditEvent(operation, result.id))

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._audit)

    @property
    def max_attempts(self) -> int:
        return self._policy.max_attempts

    def counts_by_state(self) -> dict[TaskState, int]:
        counts = Counter(task.state for task in self._repository.list())
        return {state: counts.get(state, 0) for state in TaskState}

    def describe_handler(self, action: TaskAction) -> dict[str, str]:
        handler = self._handlers[action]
        method = handler.execute
        return {
            "class": type(handler).__name__,
            "module": type(handler).__module__,
            "signature": str(inspect.signature(method)),
            "mro": " -> ".join(cls.__name__ for cls in type(handler).__mro__),
        }


def build_default_service(repository: TaskRepository | None = None) -> TaskService:
    """Instantiate registered handlers and compose the local relay service."""

    actual_repository = repository or InMemoryTaskRepository()
    handlers = {action: handler_type() for action, handler_type in HandlerMeta.registry().items()}
    return TaskService(actual_repository, handlers)
