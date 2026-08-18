"""Tests for the relay first-service checkpoint."""

from __future__ import annotations

import pytest

from lab_01_first_service import (
    InMemoryRelayService,
    InvalidTaskDefinition,
    InvalidTaskTransition,
    TaskAlreadyExists,
    TaskDefinition,
    TaskNotFound,
    TaskState,
    __version__,
    render_relayctl_status,
)


def test_submit_task_creates_a_queued_status_document() -> None:
    service = InMemoryRelayService()

    record = service.submit(TaskDefinition(task_id="task-17", action="rebuild-search-index"))

    assert record.task_id == "task-17"
    assert record.action == "rebuild-search-index"
    assert record.state is TaskState.QUEUED
    assert record.resource_path == "/tasks/task-17"
    assert render_relayctl_status(record) == (
        "relayctl status /tasks/task-17: queued action=rebuild-search-index"
    )
    assert service.list_tasks() == (record,)


def test_running_task_can_finish_successfully() -> None:
    service = InMemoryRelayService()
    service.submit(TaskDefinition(task_id="task-17", action="sync-blob-index"))

    running = service.start_task("task-17")
    succeeded = service.succeed_task("task-17", detail="copied 3 task definitions")

    assert running.state is TaskState.RUNNING
    assert succeeded.state is TaskState.SUCCEEDED
    assert succeeded.detail == "copied 3 task definitions"
    assert service.get_status("task-17") == succeeded


def test_running_task_can_finish_as_failed() -> None:
    service = InMemoryRelayService()
    service.submit(TaskDefinition(task_id="task-18", action="push-release"))
    service.start_task("task-18")

    failed = service.fail_task("task-18", "queue publish returned 503")

    assert failed.state is TaskState.FAILED
    assert failed.detail == "queue publish returned 503"


def test_duplicate_task_id_raises_an_explicit_domain_error() -> None:
    service = InMemoryRelayService()
    service.submit(TaskDefinition(task_id="task-17", action="rebuild-search-index"))

    with pytest.raises(TaskAlreadyExists, match="already exists"):
        service.submit(TaskDefinition(task_id="task-17", action="send-webhook"))


def test_missing_task_status_raises_an_explicit_domain_error() -> None:
    service = InMemoryRelayService()

    with pytest.raises(TaskNotFound, match="does not exist"):
        service.get_status("task-99")


@pytest.mark.parametrize(
    ("task_id", "action", "message"),
    [
        ("17", "send-webhook", "task_id must look like task-17"),
        ("task-17", "   ", "task action must not be empty"),
    ],
)
def test_invalid_task_definitions_are_rejected(
    task_id: str,
    action: str,
    message: str,
) -> None:
    service = InMemoryRelayService()

    with pytest.raises(InvalidTaskDefinition, match=message):
        service.submit(TaskDefinition(task_id=task_id, action=action))


def test_invalid_state_transitions_raise_an_explicit_domain_error() -> None:
    service = InMemoryRelayService()
    service.submit(TaskDefinition(task_id="task-17", action="sync-blob-index"))

    with pytest.raises(InvalidTaskTransition, match="cannot complete task-17 from queued"):
        service.succeed_task("task-17")

    service.start_task("task-17")
    service.succeed_task("task-17")

    with pytest.raises(InvalidTaskTransition, match="cannot start task-17 from succeeded"):
        service.start_task("task-17")


def test_version_is_exposed() -> None:
    assert __version__
