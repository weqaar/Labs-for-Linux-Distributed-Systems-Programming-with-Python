"""Functional tests for the object-oriented relay checkpoint.

These tests drive the composed service returned by ``build_default_service``
through its public methods. Each test follows one feature across a whole
workflow: submitting jobs, running them through the registered handlers and
reading the resulting states, counts and audit events.
"""

from __future__ import annotations

import pytest

from lab_05_object_oriented_design import (
    AuditEvent,
    IndexHandler,
    InMemoryTaskRepository,
    Task,
    TaskAction,
    TaskService,
    TaskState,
    build_default_service,
)


class FailingIndexHandler(IndexHandler):
    """Index handler that always raises, standing in for a broken action."""

    def execute(self, task: Task) -> str:
        raise RuntimeError(f"index backend unavailable for {task.id}")


def test_submitted_jobs_run_to_succeeded_through_the_default_service() -> None:
    service = build_default_service()

    service.submit(Task("task-17", TaskAction.INDEX, "documents"))
    service.submit(Task("task-18", TaskAction.ARCHIVE, "logs"))
    before = service.counts_by_state()
    first = service.run("task-17")
    second = service.run("task-18")

    assert before[TaskState.QUEUED] == 2
    assert (first.state, second.state) == (TaskState.SUCCEEDED, TaskState.SUCCEEDED)
    assert first.is_terminal and second.is_terminal
    assert service.counts_by_state() == {
        TaskState.QUEUED: 0,
        TaskState.RUNNING: 0,
        TaskState.SUCCEEDED: 2,
        TaskState.FAILED: 0,
    }
    assert service.audit_events == (
        AuditEvent("submit", "task-17"),
        AuditEvent("submit", "task-18"),
        AuditEvent("run", "task-17"),
        AuditEvent("run", "task-18"),
    )


def test_duplicate_job_id_is_rejected_and_the_first_job_is_kept() -> None:
    repository = InMemoryTaskRepository()
    service = build_default_service(repository)
    original = service.submit(Task("task-17", TaskAction.INDEX, "documents"))

    with pytest.raises(ValueError, match="task already exists: task-17"):
        service.submit(Task("task-17", TaskAction.ARCHIVE, "logs"))

    assert repository.list() == (original,)
    assert service.audit_events == (AuditEvent("submit", "task-17"),)


def test_running_an_unknown_job_is_reported_as_not_found() -> None:
    service = build_default_service()

    with pytest.raises(LookupError, match="task not found: task-99"):
        service.run("task-99")

    assert service.audit_events == ()
    assert service.counts_by_state()[TaskState.RUNNING] == 0


def test_handler_exception_reaches_the_caller_and_leaves_the_job_running() -> None:
    repository = InMemoryTaskRepository()
    service = TaskService(repository, {TaskAction.INDEX: FailingIndexHandler()})
    service.submit(Task("task-17", TaskAction.INDEX, "documents"))

    with pytest.raises(RuntimeError, match="index backend unavailable for task-17"):
        service.run("task-17")

    assert repository.get("task-17").state is TaskState.RUNNING
    assert service.counts_by_state()[TaskState.SUCCEEDED] == 0
    assert service.audit_events == (AuditEvent("submit", "task-17"),)
