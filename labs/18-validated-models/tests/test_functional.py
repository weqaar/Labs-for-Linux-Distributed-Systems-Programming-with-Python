"""Functional tests for the validated job workflow.

These tests drive the public package interface the way the `/tasks` HTTP
adapter would: raw JSON request bodies go through the boundary models, the
validated values go to `RelayTaskService`, and the job status and events come
back out as JSON. Each test follows one job from the request body to the
state a client reads.
"""

# pyright: strict

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lab_18_validated_models import (
    RelayTaskService,
    TaskEvent,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    TaskSubmission,
    validation_error_response,
)

SUBMISSION_BODY = (
    '{"id": "task-17", "action": "index", "target": "blob://relay/inbox/17",'
    ' "submitted_at": "2026-08-18T05:52:44Z"}'
)
STARTED = datetime(2026, 8, 18, 5, 53, 44, tzinfo=timezone.utc)
FINISHED = datetime(2026, 8, 18, 5, 54, 44, tzinfo=timezone.utc)


def test_submitted_json_job_runs_to_succeeded_with_ordered_events() -> None:
    service = RelayTaskService()

    queued, accepted = service.submit(TaskSubmission.model_validate_json(SUBMISSION_BODY))
    running, started = service.transition("task-17", TaskState.RUNNING, STARTED, "worker started")
    done, finished = service.transition("task-17", TaskState.SUCCEEDED, FINISHED, "worker done")
    response_body = service.status("task-17").model_dump_json()
    client_view = TaskStatus.model_validate_json(response_body)

    assert [queued.state, running.state, done.state] == [
        TaskState.QUEUED,
        TaskState.RUNNING,
        TaskState.SUCCEEDED,
    ]
    assert [event.sequence for event in (accepted, started, finished)] == [1, 2, 3]
    assert [event.state for event in (accepted, started, finished)] == [
        TaskState.QUEUED,
        TaskState.RUNNING,
        TaskState.SUCCEEDED,
    ]
    assert client_view == done
    assert client_view.updated_at == FINISHED


def test_failed_job_event_reaches_an_older_reader_with_extra_fields() -> None:
    service = RelayTaskService()
    service.submit(TaskSubmission.model_validate_json(SUBMISSION_BODY))

    status, event = service.transition("task-17", TaskState.FAILED, FINISHED, "target missing")
    published = event.model_dump(mode="json")
    published["worker"] = "relay-west-2"
    received = TaskEvent.model_validate(published)

    assert status.state is TaskState.FAILED
    assert received.state is TaskState.FAILED
    assert received.detail == "target missing"
    assert received.model_extra == {"worker": "relay-west-2"}


@pytest.mark.parametrize(
    ("body", "location"),
    [
        (
            '{"id": "task-17", "action": "index", "target": "blob://relay/inbox/17",'
            ' "submitted_at": "2026-08-18T05:52:44Z", "api_token": "top-secret-token"}',
            ("api_token",),
        ),
        (
            '{"id": "task-17", "action": "index", "target": "blob://relay/inbox/17",'
            ' "submitted_at": "2026-08-18T06:52:44+01:00"}',
            ("submitted_at",),
        ),
        (
            '{"id": "job-17", "action": "index", "target": "blob://relay/inbox/17",'
            ' "submitted_at": "2026-08-18T05:52:44Z"}',
            ("id",),
        ),
    ],
)
def test_rejected_request_body_gets_a_422_and_creates_no_job(
    body: str, location: tuple[str, ...]
) -> None:
    service = RelayTaskService()

    with pytest.raises(ValidationError) as excinfo:
        service.submit(TaskSubmission.model_validate_json(body))
    response = validation_error_response(excinfo.value)

    assert response.status == 422
    assert response.errors[0].location == location
    assert "top-secret-token" not in response.model_dump_json()
    with pytest.raises(TaskNotFoundError, match="task-17"):
        service.status("task-17")


def test_rejected_transition_leaves_the_job_status_unchanged() -> None:
    service = RelayTaskService()
    queued, _ = service.submit(TaskSubmission.model_validate_json(SUBMISSION_BODY))
    before_submission = datetime(2026, 8, 18, 5, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="timezone aware"):
        service.transition("task-17", TaskState.RUNNING, datetime(2026, 8, 18, 6, 0), "started")
    with pytest.raises(ValidationError, match="updated_at must be on or after"):
        service.transition("task-17", TaskState.RUNNING, before_submission, "started")
    with pytest.raises(TaskNotFoundError, match="task-404"):
        service.transition("task-404", TaskState.RUNNING, STARTED, "started")

    assert service.status("task-17") == queued
