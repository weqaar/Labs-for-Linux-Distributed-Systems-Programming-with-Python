"""FastAPI ASGI surface for the relay `/tasks` contract.

The fake transport in ``rpc`` exists to make retry and idempotency behaviour
deterministic to test. This module is the other half of the checkpoint: a
real ASGI application, built on the same domain contract, that a real Uvicorn
process can serve. The two do not share code because they answer different
questions, but they answer them about the same `/tasks` resource, the same
task IDs and the same states.
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

#: Bounds on header and body input. A header or field is otherwise
#: attacker-controlled and unbounded, and this module should reject an
#: oversized value with a clean 400 or 422 before it ever reaches a Python
#: dict, a cache key or a domain object.
MAX_HEADER_VALUE_LENGTH = 256
MAX_BUDGET_DIGITS = 10
MAX_BUDGET_MS = 86_400_000  # one day, generous for a remaining request budget


class TaskSubmissionBody(BaseModel):
    """Structural validation for the POST /tasks JSON body.

    Pydantic checks shape and type here: a string ID, one of the known action
    values, a non-empty-looking target and a parsable datetime, plus the same
    length ceilings the shared contract enforces on ``id`` and ``target`` so
    an oversized value is rejected as a 422 before any domain code runs. It
    does not check the domain rules from the shared contract, such as the
    ``task-<positive integer>`` ID pattern or a zero UTC offset; those still
    run through :class:`TaskSubmission` below, so a request can fail either
    check for a different reason.
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

        Pydantic's ``datetime`` field type parses a raw string directly, so
        the shared contract's ``MAX_TIMESTAMP_LENGTH`` bound is otherwise
        never applied here: this "before" validator runs on the raw value,
        ahead of that parse, so an attacker-sized string is rejected on
        shape rather than spending time being parsed into a ``datetime``
        first.
        """

        if isinstance(value, str) and len(value) > MAX_TIMESTAMP_LENGTH:
            raise ValueError(f"submitted_at must be at most {MAX_TIMESTAMP_LENGTH} characters")
        return value


class RelayTaskState:
    """In-process task store and replay cache owned by one worker.

    Uvicorn may run several worker processes for one deployment. Each one
    would construct its own :class:`RelayTaskState`, and none of them share
    this dictionary or this replay cache: a retry routed to a different
    worker than the one that saw the first attempt would not find its cached
    reply here. That is a deliberate limit of this checkpoint, not a bug; a
    production deployment needs an external store for both.
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
        """Apply a submission once, replaying a cached reply on a retry.

        This does no I/O, so it stays a plain method rather than a coroutine:
        an async route may call synchronous, non-blocking code directly
        without inventing an ``await`` for it. Only genuinely blocking work
        needs the treatment in :mod:`lab_19_rpc_service.concurrency`.
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
    """Reject a request before the route body runs if it has no correlation ID.

    A header that is present but blank, all whitespace, is rejected the same
    way as a missing one: a whitespace string is not a usable identifier, and
    accepting it would let every blank caller collide under the same
    effectively empty correlation ID.
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

    The digit-count check runs before ``int()`` ever parses the string, so an
    absurdly long digit string cannot spend time or memory being converted
    before this function has a chance to reject it; the value ceiling then
    catches an integer that parses cleanly but names a deadline with no
    realistic meaning.
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

    A mutating request with no key is legal, since only a retry needs one to
    replay safely. A key that is present but blank, all whitespace, is
    rejected rather than stored as a cache key: an empty-looking identifier
    would let unrelated blank requests collide in the replay cache. A key
    that is present but oversized is rejected for the same reason the
    correlation ID is, since it is stored as a cache key and must not grow
    the replay cache without bound.
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

    Accepting an injected state, rather than reaching for a module-level
    global, lets tests build several independent apps in one process without
    one test's tasks leaking into another's.
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

    This is the one import target :mod:`lab_19_rpc_service.server` names,
    referenced as ``lab_19_rpc_service.api:app_factory`` and served with
    Uvicorn's ``factory=True``. Uvicorn calls it once per worker process, so
    running with several workers gives each one its own freshly built
    :class:`RelayTaskState` rather than sharing one built at import time.
    """

    return create_app()
