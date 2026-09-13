"""Typed relay service code that stays on validated models."""

from __future__ import annotations

from datetime import datetime

from pydantic import validate_call

from lab_18_validated_models.models import (
    TaskDetail,
    TaskEvent,
    TaskId,
    TaskSequence,
    TaskState,
    TaskStatus,
    TaskSubmission,
    ensure_utc,
)


class TaskNotFoundError(KeyError):
    """Raised when a task ID does not exist in the in-memory store."""


class RelayTaskService:
    """Small in-memory relay service that uses validated models end to end."""

    def __init__(self) -> None:
        self._events_emitted = 0
        self._tasks: dict[str, TaskStatus] = {}

    @validate_call
    def submit(self, submission: TaskSubmission) -> tuple[TaskStatus, TaskEvent]:
        """Accept one task submission and create the queued task record."""

        status = TaskStatus(
            id=submission.id,
            action=submission.action,
            target=submission.target,
            state=TaskState.QUEUED,
            submitted_at=submission.submitted_at,
            updated_at=submission.submitted_at,
        )
        self._tasks[submission.id] = status
        return status, self._build_event(status=status, detail="accepted")

    @validate_call
    def status(self, task_id: TaskId) -> TaskStatus:
        """Return the current task status."""

        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc

    @validate_call
    def transition(
        self,
        task_id: TaskId,
        state: TaskState,
        when: datetime,
        detail: TaskDetail,
    ) -> tuple[TaskStatus, TaskEvent]:
        """Apply one state transition with a UTC timestamp."""

        current = self.status(task_id)
        updated = TaskStatus.model_validate(
            {
                **current.model_dump(mode="python"),
                "state": state,
                "updated_at": ensure_utc(when),
            }
        )
        self._tasks[task_id] = updated
        return updated, self._build_event(status=updated, detail=detail)

    def _build_event(self, *, status: TaskStatus, detail: str) -> TaskEvent:
        sequence = self._next_sequence()
        return TaskEvent(
            sequence=sequence,
            id=status.id,
            action=status.action,
            state=status.state,
            timestamp=status.updated_at,
            detail=detail,
        )

    def _next_sequence(self) -> TaskSequence:
        self._events_emitted += 1
        return self._events_emitted
