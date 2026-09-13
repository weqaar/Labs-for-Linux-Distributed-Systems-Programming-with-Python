"""Pydantic boundary models for the relay task service."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

TASKS_COLLECTION_PATH = "/tasks"
TASK_ID_PATTERN = r"^task-[1-9][0-9]*$"

TaskId = Annotated[StrictStr, Field(pattern=TASK_ID_PATTERN)]
TaskTarget = Annotated[StrictStr, Field(min_length=1, max_length=200)]
TaskDetail = Annotated[StrictStr, Field(min_length=1, max_length=200)]
TaskSequence = Annotated[StrictInt, Field(ge=1)]


class TaskAction(str, Enum):
    """Supported relay actions."""

    INDEX = "index"
    DELIVER = "deliver"
    ARCHIVE = "archive"


class TaskState(str, Enum):
    """Task states shared by every relay checkpoint."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UnknownEnvironmentVariableError(ValueError):
    """Raised when a relay setting name is not recognised."""


class StrictBoundaryModel(BaseModel):
    """Base model for strict relay boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ForwardCompatibleBoundaryModel(BaseModel):
    """Base model for message boundaries that keep unknown fields."""

    model_config = ConfigDict(extra="allow", frozen=True)


class RelaySettings(StrictBoundaryModel):
    """Process settings loaded once at startup."""

    api_base_url: Annotated[StrictStr, Field(pattern=r"^https?://", min_length=1)] = (
        "https://relay.example"
    )
    request_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 30


class TaskSubmission(StrictBoundaryModel):
    """HTTP POST /tasks request body."""

    id: TaskId
    action: TaskAction
    target: TaskTarget
    submitted_at: datetime

    @field_validator("submitted_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class TaskStatus(StrictBoundaryModel):
    """HTTP GET /tasks/{id} response body."""

    id: TaskId
    action: TaskAction
    target: TaskTarget
    state: TaskState
    submitted_at: datetime
    updated_at: datetime

    @field_validator("submitted_at", "updated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def ensure_timestamp_order(self) -> TaskStatus:
        if self.updated_at < self.submitted_at:
            raise ValueError("updated_at must be on or after submitted_at")
        return self


class TaskEvent(ForwardCompatibleBoundaryModel):
    """Queue or stream event emitted after a task change."""

    sequence: TaskSequence
    id: TaskId
    action: TaskAction
    state: TaskState
    timestamp: datetime
    detail: TaskDetail

    @field_validator("timestamp")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ValidationProblem(StrictBoundaryModel):
    """One validation failure rendered without echoing the offending input."""

    location: tuple[str | int, ...]
    message: StrictStr
    error_type: StrictStr


class ValidationErrorResponse(StrictBoundaryModel):
    """Structured 422 response body."""

    status: Literal[422] = 422
    title: Literal["Validation failed"] = "Validation failed"
    errors: tuple[ValidationProblem, ...]


def ensure_utc(value: datetime) -> datetime:
    """Reject naive or non-UTC timestamps and normalise zero offsets."""

    offset = value.utcoffset()
    if offset is None:
        raise ValueError("timestamp must be timezone aware")
    if offset != timedelta(0):
        raise ValueError("timestamp must be in UTC")
    return value.astimezone(timezone.utc)


def load_settings_from_environment(environ: Mapping[str, str]) -> RelaySettings:
    """Load relay settings and reject unknown RELAY_* names."""

    known_names = {
        "RELAY_API_BASE_URL": "api_base_url",
        "RELAY_REQUEST_TIMEOUT_SECONDS": "request_timeout_seconds",
    }
    unknown = sorted(key for key in environ if key.startswith("RELAY_") and key not in known_names)
    if unknown:
        listed = ", ".join(unknown)
        raise UnknownEnvironmentVariableError(f"unknown relay environment variables: {listed}")

    payload = {
        field_name: environ[key] for key, field_name in known_names.items() if key in environ
    }
    return RelaySettings.model_validate_strings(payload, strict=True)


def validation_error_response(error: ValidationError) -> ValidationErrorResponse:
    """Render a structured 422 body without the bad input values."""

    problems = tuple(
        ValidationProblem(
            location=tuple(item["loc"]),
            message=item["msg"],
            error_type=item["type"],
        )
        for item in error.errors(include_input=False, include_url=False)
    )
    return ValidationErrorResponse(errors=problems)


def boundary_json_schemas() -> dict[str, dict[str, Any]]:
    """Return stable JSON Schemas for the relay boundaries."""

    return {
        "TaskSubmission": TaskSubmission.model_json_schema(),
        "TaskStatus": TaskStatus.model_json_schema(),
        "TaskEvent": TaskEvent.model_json_schema(),
        "RelaySettings": RelaySettings.model_json_schema(),
        "ValidationErrorResponse": ValidationErrorResponse.model_json_schema(),
    }
