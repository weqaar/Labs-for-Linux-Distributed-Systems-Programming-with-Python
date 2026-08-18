"""Checkpoint 01 for relay: an in-memory `/tasks` core for `relayctl`."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum

TASK_ID_PATTERN = re.compile(r"task-\d+\Z")


class TaskState(str, Enum):
    """States exposed by the relay task service."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskDefinition:
    """The input document that creates a task in relay."""

    task_id: str
    action: str


@dataclass(frozen=True)
class TaskRecord:
    """The status document served back from `/tasks/<task-id>`."""

    task_id: str
    action: str
    state: TaskState
    detail: str | None = None

    @property
    def resource_path(self) -> str:
        return f"/tasks/{self.task_id}"


class RelayDomainError(Exception):
    """Base class for relay domain errors."""


class InvalidTaskDefinition(RelayDomainError):
    """Raised when a relay task definition is invalid."""


class TaskAlreadyExists(RelayDomainError):
    """Raised when a task ID is submitted twice."""


class TaskNotFound(RelayDomainError):
    """Raised when a task status is requested for a missing task."""


class InvalidTaskTransition(RelayDomainError):
    """Raised when code asks relay for an impossible state change."""


class InMemoryRelayService:
    """First relay checkpoint used before HTTP and Azure arrive."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    def submit(self, definition: TaskDefinition) -> TaskRecord:
        task_id = _validate_task_id(definition.task_id)
        action = _validate_action(definition.action)
        if task_id in self._tasks:
            raise TaskAlreadyExists(f"task {task_id} already exists")
        record = TaskRecord(task_id=task_id, action=action, state=TaskState.QUEUED)
        self._tasks[task_id] = record
        return record

    def get_status(self, task_id: str) -> TaskRecord:
        return self._get(task_id)

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        return tuple(self._tasks.values())

    def start_task(self, task_id: str) -> TaskRecord:
        task = self._require_state(task_id, expected=TaskState.QUEUED, transition="start")
        updated = replace(task, state=TaskState.RUNNING, detail=None)
        self._tasks[task_id] = updated
        return updated

    def succeed_task(self, task_id: str, detail: str | None = None) -> TaskRecord:
        task = self._require_state(task_id, expected=TaskState.RUNNING, transition="complete")
        cleaned_detail = _clean_optional_detail(detail)
        updated = replace(task, state=TaskState.SUCCEEDED, detail=cleaned_detail)
        self._tasks[task_id] = updated
        return updated

    def fail_task(self, task_id: str, reason: str) -> TaskRecord:
        task = self._require_state(task_id, expected=TaskState.RUNNING, transition="fail")
        updated = replace(task, state=TaskState.FAILED, detail=_validate_reason(reason))
        self._tasks[task_id] = updated
        return updated

    def _get(self, task_id: str) -> TaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise TaskNotFound(f"task {task_id} does not exist") from error

    def _require_state(
        self,
        task_id: str,
        *,
        expected: TaskState,
        transition: str,
    ) -> TaskRecord:
        task = self._get(task_id)
        if task.state is not expected:
            raise InvalidTaskTransition(f"cannot {transition} {task_id} from {task.state.value}")
        return task


def render_relayctl_status(task: TaskRecord) -> str:
    """Return the line `relayctl` would print for a task status check."""

    return f"relayctl status {task.resource_path}: {task.state.value} action={task.action}"


def _validate_task_id(task_id: str) -> str:
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise InvalidTaskDefinition("task_id must look like task-17")
    return task_id


def _validate_action(action: str) -> str:
    cleaned = action.strip()
    if not cleaned:
        raise InvalidTaskDefinition("task action must not be empty")
    return cleaned


def _validate_reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned:
        raise InvalidTaskDefinition("failure reason must not be empty")
    return cleaned


def _clean_optional_detail(detail: str | None) -> str | None:
    if detail is None:
        return None
    cleaned = detail.strip()
    return cleaned or None
