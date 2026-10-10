"""Functional tests for the relay first-service checkpoint.

These tests drive the public interface of the package, the
``InMemoryRelayService`` class together with ``render_relayctl_status``, across
whole job lifecycles. Each test follows one feature from submission to a
terminal state, the way a caller of this checkpoint would use it.
"""

from __future__ import annotations

import pytest

from lab_01_first_service import (
    InMemoryRelayService,
    InvalidTaskTransition,
    TaskAlreadyExists,
    TaskDefinition,
    TaskState,
    render_relayctl_status,
)


def test_submitted_job_runs_to_succeeded_and_reports_through_relayctl_line() -> None:
    service = InMemoryRelayService()

    queued = service.submit(TaskDefinition(task_id="task-17", action="rebuild-search-index"))
    lines = [render_relayctl_status(service.get_status("task-17"))]
    service.start_task("task-17")
    lines.append(render_relayctl_status(service.get_status("task-17")))
    service.succeed_task("task-17", detail="  indexed 42 documents  ")
    final = service.get_status("task-17")
    lines.append(render_relayctl_status(final))

    assert lines == [
        "relayctl status /tasks/task-17: queued action=rebuild-search-index",
        "relayctl status /tasks/task-17: running action=rebuild-search-index",
        "relayctl status /tasks/task-17: succeeded action=rebuild-search-index",
    ]
    assert final.detail == "indexed 42 documents"
    assert queued.state is TaskState.QUEUED
    assert service.list_tasks() == (final,)


def test_failed_job_keeps_its_reason_while_other_jobs_continue() -> None:
    service = InMemoryRelayService()
    service.submit(TaskDefinition(task_id="task-17", action="push-release"))
    service.submit(TaskDefinition(task_id="task-18", action="send-webhook"))

    service.start_task("task-17")
    service.fail_task("task-17", "queue publish returned 503")
    service.start_task("task-18")
    service.succeed_task("task-18")

    states = {record.task_id: record.state for record in service.list_tasks()}
    assert states == {"task-17": TaskState.FAILED, "task-18": TaskState.SUCCEEDED}
    assert service.get_status("task-17").detail == "queue publish returned 503"
    with pytest.raises(InvalidTaskTransition, match="cannot start task-17 from failed"):
        service.start_task("task-17")


def test_duplicate_submission_is_rejected_without_disturbing_the_running_job() -> None:
    service = InMemoryRelayService()
    service.submit(TaskDefinition(task_id="task-17", action="rebuild-search-index"))
    running = service.start_task("task-17")

    with pytest.raises(TaskAlreadyExists, match="task task-17 already exists"):
        service.submit(TaskDefinition(task_id="task-17", action="send-webhook"))

    assert service.get_status("task-17") == running
    assert service.list_tasks() == (running,)


def test_completing_a_job_that_never_started_is_rejected_and_leaves_it_queued() -> None:
    service = InMemoryRelayService()
    queued = service.submit(TaskDefinition(task_id="task-17", action="sync-blob-index"))

    with pytest.raises(InvalidTaskTransition, match="cannot complete task-17 from queued"):
        service.succeed_task("task-17", detail="skipped ahead")
    with pytest.raises(InvalidTaskTransition, match="cannot fail task-17 from queued"):
        service.fail_task("task-17", "skipped ahead")

    assert service.get_status("task-17") == queued
    assert render_relayctl_status(service.get_status("task-17")) == (
        "relayctl status /tasks/task-17: queued action=sync-blob-index"
    )
