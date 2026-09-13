"""Tests for the validated relay task boundaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lab_18_validated_models import (
    TASKS_COLLECTION_PATH,
    RelayTaskService,
    TaskAction,
    TaskEvent,
    TaskNotFoundError,
    TaskState,
    TaskSubmission,
    UnknownEnvironmentVariableError,
    __version__,
    boundary_json_schemas,
    load_settings_from_environment,
    validation_error_response,
)


def test_submission_parses_json_and_normalises_utc_timestamps() -> None:
    payload = {
        "id": "task-17",
        "action": "index",
        "target": "blob://relay/inbox/17",
        "submitted_at": "2026-08-18T05:52:44Z",
    }

    submission = TaskSubmission.model_validate_json(json.dumps(payload))

    assert submission.id == "task-17"
    assert submission.action is TaskAction.INDEX
    assert submission.submitted_at == datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("payload", "location", "error_type"),
    [
        (
            {
                "id": 17,
                "action": "index",
                "target": "blob://relay/inbox/17",
                "submitted_at": "2026-08-18T05:52:44Z",
            },
            ("id",),
            "string_type",
        ),
        (
            {
                "id": "task-17",
                "action": "index",
                "target": "blob://relay/inbox/17",
                "submitted_at": "2026-08-18T05:52:44Z",
                "priority": 7,
            },
            ("priority",),
            "extra_forbidden",
        ),
    ],
)
def test_submission_rejects_wrong_types_and_extra_fields(
    payload: dict[str, object],
    location: tuple[str, ...],
    error_type: str,
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        TaskSubmission.model_validate(payload)

    first_error = excinfo.value.errors(include_input=False, include_url=False)[0]
    assert tuple(first_error["loc"]) == location
    assert first_error["type"] == error_type


def test_submission_rejects_non_utc_timestamps() -> None:
    payload = {
        "id": "task-17",
        "action": "index",
        "target": "blob://relay/inbox/17",
        "submitted_at": "2026-08-18T06:52:44+01:00",
    }

    with pytest.raises(ValidationError) as excinfo:
        TaskSubmission.model_validate(payload)

    response = validation_error_response(excinfo.value)
    assert response.errors[0].location == ("submitted_at",)
    assert "UTC" in response.errors[0].message


def test_event_keeps_unknown_future_fields() -> None:
    event = TaskEvent.model_validate(
        {
            "sequence": 3,
            "id": "task-17",
            "action": "index",
            "state": "running",
            "timestamp": "2026-08-18T05:53:44Z",
            "detail": "worker picked task",
            "worker": "relay-west-2",
        }
    )

    assert event.state is TaskState.RUNNING
    assert event.model_extra == {"worker": "relay-west-2"}


def test_boundary_json_schemas_publish_the_extra_field_policy() -> None:
    schemas = boundary_json_schemas()

    assert TASKS_COLLECTION_PATH == "/tasks"
    assert schemas["TaskSubmission"]["additionalProperties"] is False
    assert schemas["TaskStatus"]["additionalProperties"] is False
    assert schemas["TaskEvent"].get("additionalProperties", True) is True
    assert schemas["TaskSubmission"]["properties"]["id"]["pattern"] == r"^task-[1-9][0-9]*$"


def test_settings_reject_unknown_environment_variables() -> None:
    with pytest.raises(UnknownEnvironmentVariableError, match="RELAY_SECRET"):
        load_settings_from_environment(
            {
                "RELAY_API_BASE_URL": "https://relay.example",
                "RELAY_REQUEST_TIMEOUT_SECONDS": "15",
                "RELAY_SECRET": "do-not-accept",
            }
        )

    settings = load_settings_from_environment(
        {
            "RELAY_API_BASE_URL": "https://relay.example",
            "RELAY_REQUEST_TIMEOUT_SECONDS": "15",
        }
    )
    assert settings.request_timeout_seconds == 15


def test_validation_error_response_omits_the_offending_input() -> None:
    bad_payload = {
        "id": "task-17",
        "action": "index",
        "target": "blob://relay/inbox/17",
        "submitted_at": "2026-08-18T05:52:44Z",
        "api_token": "top-secret-token",
    }

    with pytest.raises(ValidationError) as excinfo:
        TaskSubmission.model_validate(bad_payload)

    response = validation_error_response(excinfo.value)
    rendered = response.model_dump_json()

    assert response.status == 422
    assert response.errors[0].location == ("api_token",)
    assert "top-secret-token" not in rendered


def test_service_stays_on_models_after_validation() -> None:
    service = RelayTaskService()
    submission = TaskSubmission.model_validate(
        {
            "id": "task-17",
            "action": "index",
            "target": "blob://relay/inbox/17",
            "submitted_at": "2026-08-18T05:52:44Z",
        }
    )

    queued, accepted = service.submit(submission)
    completed, finished = service.transition(
        "task-17",
        TaskState.SUCCEEDED,
        datetime(2026, 8, 18, 5, 54, 44, tzinfo=timezone.utc),
        "worker finished task",
    )

    assert queued.state is TaskState.QUEUED
    assert accepted.sequence == 1
    assert completed.state is TaskState.SUCCEEDED
    assert finished.sequence == 2
    assert service.status("task-17") == completed


def test_service_raises_a_narrow_error_for_unknown_tasks() -> None:
    service = RelayTaskService()

    with pytest.raises(TaskNotFoundError, match="task-404"):
        service.status("task-404")


def test_version_is_exposed() -> None:
    assert __version__
