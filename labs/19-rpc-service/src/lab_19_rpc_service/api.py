"""Serve relay submissions and status requests through FastAPI.

Uvicorn can serve this ASGI application over HTTP. Tests use TestClient in
process; the separate fake transport in rpc can simulate request and reply
loss. Both implementations share task models and the replay-cache class.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lab_19_rpc_service.contract import (
    MAX_TARGET_LENGTH,
    MAX_TASK_ID_LENGTH,
    MAX_TIMESTAMP_LENGTH,
    TASKS_COLLECTION_PATH,
    ContractError,
    TaskAction,
    TaskState,
    TaskStatus,
    TaskSubmission,
)
from lab_19_rpc_service.rpc import CachedReply, IdempotencyConflict, ReplayCache

CORRELATION_HEADER = "x-correlation-id"
BUDGET_HEADER = "x-relay-budget-ms"
IDEMPOTENCY_HEADER = "idempotency-key"

#: Limit retained header values and budget conversion work. These checks do not
#: limit the full request body or the number of stored tasks and cache entries.
MAX_HEADER_VALUE_LENGTH = 256
MAX_BUDGET_DIGITS = 10
MAX_BUDGET_MS = 86_400_000  # one day, generous for a remaining request budget


class TaskSubmissionBody(BaseModel):
    """Structural validation for the POST /tasks JSON body.

    Pydantic checks field types, known actions and length limits, and rejects
    extra fields. TaskSubmission then checks the task-ID pattern, nonblank
    target and UTC timestamp. Model errors produce HTTP 422; these later
    domain checks produce HTTP 400.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=MAX_TASK_ID_LENGTH)
    action: TaskAction
    target: str = Field(max_length=MAX_TARGET_LENGTH)
    submitted_at: datetime

    @field_validator("submitted_at", mode="before")
    @classmethod
    def _bound_submitted_at_length(cls, value: Any) -> Any:
        """Reject an oversized timestamp string before Pydantic parses it.

        After conversion to datetime the original string length is lost, so
        this check must run on the raw value.
        """

        if isinstance(value, str) and len(value) > MAX_TIMESTAMP_LENGTH:
            raise ValueError(f"submitted_at must be at most {MAX_TIMESTAMP_LENGTH} characters")
        return value


class RelayTaskState:
    """In-process task store and replay cache owned by one worker.

    Each Uvicorn worker constructs a separate instance. A retry routed to
    another worker cannot find the first worker's cached reply. Multiple
    workers need shared task and replay storage to preserve that behavior.
    This in-memory implementation also has no persistence or retention limit.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskStatus] = {}
        self._replay_cache = ReplayCache()
        self.effects_applied = 0
        self.replayed_requests = 0
        self.observed_budgets: list[int] = []

    def submit(
        self,
        submission: TaskSubmission,
        *,
        idempotency_key: str | None,
    ) -> tuple[TaskStatus, bool]:
        """Store a submission or replay the response for a matching key and body.

        Return the status and whether it was replayed. Reusing a key with a
        different body raises IdempotencyConflict. Without a matching cache
        entry, an existing task with the same ID is overwritten.

        This method performs no I/O, so an async route calls it directly.
        """

        fingerprint = _fingerprint(submission.to_mapping())
        if idempotency_key is not None:
            cached = self._replay_cache.get(idempotency_key, fingerprint)
            if cached is not None:
                self.replayed_requests += 1
                return TaskStatus.from_mapping(cached.body), True

        status = TaskStatus(
            id=submission.id,
            action=submission.action,
            target=submission.target,
            state=TaskState.QUEUED,
            submitted_at=submission.submitted_at,
            updated_at=submission.submitted_at,
        )
        self._tasks[status.id] = status
        self.effects_applied += 1
        if idempotency_key is not None:
            reply = CachedReply(status_code=202, body=status.to_mapping())
            self._replay_cache.store(idempotency_key, fingerprint, reply)
        return status, False

    def get(self, task_id: str) -> TaskStatus | None:
        return self._tasks.get(task_id)

    def record_budget(self, budget_ms: int) -> None:
        self.observed_budgets.append(budget_ms)


def require_correlation_id(
    x_correlation_id: Annotated[str | None, Header(alias=CORRELATION_HEADER)] = None,
) -> str:
    """Return the correlation header unchanged after checking its length and content.

    Missing, blank or oversized values raise HTTPException with status 400.
    """

    if x_correlation_id is None or not x_correlation_id.strip():
        raise HTTPException(
            status_code=400, detail=f"missing required header: {CORRELATION_HEADER}"
        )
    if len(x_correlation_id) > MAX_HEADER_VALUE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"{CORRELATION_HEADER} must be at most {MAX_HEADER_VALUE_LENGTH} characters",
        )
    return x_correlation_id


def require_budget_ms(
    x_relay_budget_ms: Annotated[str | None, Header(alias=BUDGET_HEADER)] = None,
) -> int:
    """Reject a request before the route body runs if its budget is malformed.

    Return the parsed budget in milliseconds. Length is checked before integer
    conversion, and the value cannot exceed MAX_BUDGET_MS. This parses a header;
    it does not enforce elapsed request time.
    """

    if x_relay_budget_ms is None or not x_relay_budget_ms.isdigit() or x_relay_budget_ms == "0":
        raise HTTPException(status_code=400, detail=f"{BUDGET_HEADER} must be a positive integer")
    if len(x_relay_budget_ms) > MAX_BUDGET_DIGITS:
        raise HTTPException(
            status_code=400,
            detail=f"{BUDGET_HEADER} must be at most {MAX_BUDGET_DIGITS} digits",
        )
    budget_ms = int(x_relay_budget_ms)
    if budget_ms > MAX_BUDGET_MS:
        raise HTTPException(
            status_code=400,
            detail=f"{BUDGET_HEADER} must be at most {MAX_BUDGET_MS} milliseconds",
        )
    return budget_ms


def optional_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> str | None:
    """Bound an idempotency key header without requiring one.

    Return None when absent, or the unchanged key when valid. Blank or oversized
    keys raise HTTPException with status 400. Safe replay requires the same key
    on the original submission and every retry, not just on the retry.
    Limiting key length does not bound the number of cache entries.
    """

    if idempotency_key is None:
        return None
    if not idempotency_key.strip():
        raise HTTPException(status_code=400, detail=f"{IDEMPOTENCY_HEADER} must not be blank")
    if len(idempotency_key) > MAX_HEADER_VALUE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"{IDEMPOTENCY_HEADER} must be at most {MAX_HEADER_VALUE_LENGTH} characters",
        )
    return idempotency_key


def create_app(state: RelayTaskState | None = None) -> FastAPI:
    """Build the relay ASGI application.

    Use the supplied state, or create a fresh in-memory store and replay cache.
    Separate default instances keep one test's tasks out of another test's app.
    """

    state = RelayTaskState() if state is None else state
    app = FastAPI(title="relay-rpc-service")
    app.state.relay = state

    @app.post(TASKS_COLLECTION_PATH, status_code=202)
    async def submit_task(
        body: TaskSubmissionBody,
        correlation_id: Annotated[str, Depends(require_correlation_id)],
        budget_ms: Annotated[int, Depends(require_budget_ms)],
        idempotency_key: Annotated[str | None, Depends(optional_idempotency_key)] = None,
    ) -> JSONResponse:
        state.record_budget(budget_ms)
        try:
            submission = TaskSubmission(
                id=body.id,
                action=body.action,
                target=body.target,
                submitted_at=body.submitted_at,
            )
        except ContractError as exc:
            return _error_response(400, str(exc), correlation_id)

        try:
            status, replayed = state.submit(submission, idempotency_key=idempotency_key)
        except IdempotencyConflict as exc:
            return _error_response(409, str(exc), correlation_id)

        headers = {CORRELATION_HEADER: correlation_id}
        if replayed:
            headers["x-idempotent-replay"] = "true"
        return JSONResponse(status_code=202, content=status.to_mapping(), headers=headers)

    @app.get(f"{TASKS_COLLECTION_PATH}/{{task_id}}")
    async def get_task(
        task_id: Annotated[str, Path(max_length=MAX_TASK_ID_LENGTH)],
        correlation_id: Annotated[str, Depends(require_correlation_id)],
        budget_ms: Annotated[int, Depends(require_budget_ms)],
    ) -> JSONResponse:
        state.record_budget(budget_ms)
        status = state.get(task_id)
        if status is None:
            return _error_response(404, f"task not found: {task_id}", correlation_id)
        return JSONResponse(
            status_code=200,
            content=status.to_mapping(),
            headers={CORRELATION_HEADER: correlation_id},
        )

    return app


def _error_response(status_code: int, message: str, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": message},
        headers={CORRELATION_HEADER: correlation_id},
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def app_factory() -> FastAPI:
    """Zero-argument application factory for Uvicorn.

    With factory=True, Uvicorn calls this in each worker process. Every call
    returns an application with a new, independent RelayTaskState.
    """

    return create_app()
