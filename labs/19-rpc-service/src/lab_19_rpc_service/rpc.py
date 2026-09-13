"""HTTP RPC client and service core for the relay task service."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

from lab_19_rpc_service.contract import (
    TASKS_COLLECTION_PATH,
    ContractError,
    TaskState,
    TaskStatus,
    TaskSubmission,
)

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 502, 503, 504})


class RelayRpcError(RuntimeError):
    """Base class for client and server RPC failures."""


class DeadlineExceeded(RelayRpcError):
    """Raised when the caller's budget is exhausted."""


class TransportFailure(RelayRpcError):
    """Raised when the fake transport loses the call."""


class IdempotencyConflict(RelayRpcError):
    """Raised when one key is reused for different request bodies."""


class TaskNotFound(RelayRpcError):
    """Raised when the task service has no record for one task ID."""


class RequestDroppedError(TransportFailure):
    """Raised when a request never reaches the service."""


class ReplyDroppedError(TransportFailure):
    """Raised when the service reply does not reach the caller."""


@dataclass
class FakeClock:
    """Clock used by the fake transport and the fake service."""

    monotonic_ms: int = 0
    wall_time: datetime = datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc)

    def advance(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("milliseconds must not be negative")
        self.monotonic_ms += milliseconds
        self.wall_time += timedelta(milliseconds=milliseconds)


@dataclass(frozen=True)
class DeadlineBudget:
    """A caller budget measured against a monotonic clock."""

    started_ms: int
    total_ms: int

    @classmethod
    def start(cls, total_ms: int, clock: FakeClock) -> DeadlineBudget:
        if total_ms <= 0:
            raise ValueError("deadline budget must be positive")
        return cls(started_ms=clock.monotonic_ms, total_ms=total_ms)

    def remaining_ms(self, clock: FakeClock) -> int:
        elapsed = clock.monotonic_ms - self.started_ms
        return max(self.total_ms - elapsed, 0)


@dataclass(frozen=True)
class HttpRequest:
    """Minimal HTTP request used by the fake transport."""

    method: str
    path: str
    headers: Mapping[str, str]
    json_body: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.method not in {"GET", "POST"}:
            raise ValueError("unsupported method")
        if not self.path.startswith("/"):
            raise ValueError("path must start with '/'")
        object.__setattr__(self, "headers", dict(self.headers))
        if self.json_body is not None:
            object.__setattr__(self, "json_body", dict(self.json_body))


@dataclass(frozen=True)
class HttpResponse:
    """Minimal HTTP response used by the fake transport."""

    status_code: int
    headers: Mapping[str, str]
    json_body: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", dict(self.headers))
        if self.json_body is not None:
            object.__setattr__(self, "json_body", dict(self.json_body))


class TransportHandler(Protocol):
    """Callable service interface used by the fake transport."""

    def __call__(self, request: HttpRequest) -> HttpResponse: ...


class RetryOutcomeKind(str, Enum):
    """Kinds of retry inputs seen by the decision model."""

    TRANSPORT_LOSS = "transport_loss"
    HTTP_STATUS = "http_status"


@dataclass(frozen=True)
class RetryOutcome:
    """One client outcome fed into the retry decision model."""

    kind: RetryOutcomeKind
    status_code: int | None = None

    @classmethod
    def transport_loss(cls) -> RetryOutcome:
        return cls(RetryOutcomeKind.TRANSPORT_LOSS)

    @classmethod
    def http_status(cls, status_code: int) -> RetryOutcome:
        return cls(RetryOutcomeKind.HTTP_STATUS, status_code=status_code)


@dataclass(frozen=True)
class RetryDecision:
    """Decision returned by the retry model."""

    retry: bool
    reason: str


@dataclass(frozen=True)
class TransportFault:
    """One scripted fault applied to one transport send."""

    delay_ms: int = 0
    drop_request: bool = False
    drop_reply: bool = False
    duplicate_request: bool = False

    def __post_init__(self) -> None:
        if self.delay_ms < 0:
            raise ValueError("delay_ms must not be negative")


@dataclass(frozen=True)
class CachedReply:
    """Idempotent reply stored by the replay cache."""

    status_code: int
    body: Mapping[str, Any]


class ReplayCache:
    """Replay cache keyed by idempotency key and request fingerprint."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, CachedReply]] = {}

    def get(self, key: str, fingerprint: str) -> CachedReply | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        cached_fingerprint, cached_reply = entry
        if cached_fingerprint != fingerprint:
            raise IdempotencyConflict("idempotency key reused with a different request body")
        return cached_reply

    def store(self, key: str, fingerprint: str, reply: CachedReply) -> None:
        self._entries[key] = (fingerprint, reply)


class FakeTransport:
    """Deterministic transport that can drop, delay and duplicate calls."""

    def __init__(
        self,
        handler: TransportHandler,
        clock: FakeClock,
        faults: tuple[TransportFault, ...] = (),
    ) -> None:
        self._handler = handler
        self._clock = clock
        self._faults = list(faults)
        self.requests: list[HttpRequest] = []
        self.responses: list[HttpResponse] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        fault = self._faults.pop(0) if self._faults else TransportFault()
        if fault.delay_ms:
            self._clock.advance(fault.delay_ms)
        self.requests.append(request)
        if fault.drop_request:
            raise RequestDroppedError("request dropped before delivery")

        response = self._handler(request)
        self.responses.append(response)
        if fault.duplicate_request:
            replayed = self._handler(request)
            self.responses.append(replayed)
            response = replayed
        if fault.drop_reply:
            raise ReplyDroppedError("reply dropped after processing")
        return response


class RelayHttpService:
    """Fake HTTP service that implements the relay `/tasks` contract."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._tasks: dict[str, TaskStatus] = {}
        self._replay_cache = ReplayCache()
        self.effects_applied = 0
        self.replayed_requests = 0
        self.observed_budgets: list[int] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.handle(request)

    def handle(self, request: HttpRequest) -> HttpResponse:
        correlation_id = _require_header(request.headers, "x-correlation-id")
        budget_ms = _parse_positive_int(
            _require_header(request.headers, "x-relay-budget-ms"),
            field_name="x-relay-budget-ms",
        )
        self.observed_budgets.append(budget_ms)

        if request.method == "POST" and request.path == TASKS_COLLECTION_PATH:
            try:
                return self._submit_task(request, correlation_id)
            except ContractError as exc:
                return self._response(400, {"error": str(exc)}, correlation_id)
            except IdempotencyConflict as exc:
                return self._response(409, {"error": str(exc)}, correlation_id)
        if request.method == "GET" and request.path.startswith(f"{TASKS_COLLECTION_PATH}/"):
            return self._get_task(request.path, correlation_id)
        return self._response(404, {"error": "route not found"}, correlation_id)

    def _submit_task(self, request: HttpRequest, correlation_id: str) -> HttpResponse:
        body = request.json_body or {}
        idempotency_key = request.headers.get("idempotency-key")
        fingerprint = _request_fingerprint(request)
        if idempotency_key is not None:
            cached_reply = self._replay_cache.get(idempotency_key, fingerprint)
            if cached_reply is not None:
                self.replayed_requests += 1
                return self._response(
                    cached_reply.status_code,
                    cached_reply.body,
                    correlation_id,
                    replayed=True,
                )

        submission = TaskSubmission.from_mapping(body)
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
        reply = CachedReply(status_code=202, body=status.to_mapping())
        if idempotency_key is not None:
            self._replay_cache.store(idempotency_key, fingerprint, reply)
        return self._response(reply.status_code, reply.body, correlation_id)

    def _get_task(self, path: str, correlation_id: str) -> HttpResponse:
        task_id = path.rsplit("/", 1)[-1]
        status = self._tasks.get(task_id)
        if status is None:
            return self._response(404, {"error": f"task not found: {task_id}"}, correlation_id)
        return self._response(200, status.to_mapping(), correlation_id)

    def _response(
        self,
        status_code: int,
        body: Mapping[str, Any],
        correlation_id: str,
        *,
        replayed: bool = False,
    ) -> HttpResponse:
        headers = {"x-correlation-id": correlation_id}
        if replayed:
            headers["x-idempotent-replay"] = "true"
        return HttpResponse(status_code=status_code, headers=headers, json_body=body)


class RelayRpcClient:
    """Caller side of the relay RPC layer."""

    def __init__(
        self,
        transport: FakeTransport,
        clock: FakeClock,
        *,
        correlation_ids: Callable[[], str],
        max_retries: int = 2,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self._transport = transport
        self._clock = clock
        self._correlation_ids = correlation_ids
        self._max_retries = max_retries

    def submit_task(
        self,
        submission: TaskSubmission,
        *,
        budget_ms: int,
        idempotency_key: str,
    ) -> TaskStatus:
        response = self._send_with_retry(
            method="POST",
            path=TASKS_COLLECTION_PATH,
            json_body=submission.to_mapping(),
            budget_ms=budget_ms,
            idempotency_key=idempotency_key,
        )
        return TaskStatus.from_mapping(_require_body(response))

    def get_task(self, task_id: str, *, budget_ms: int) -> TaskStatus:
        response = self._send_with_retry(
            method="GET",
            path=f"{TASKS_COLLECTION_PATH}/{task_id}",
            json_body=None,
            budget_ms=budget_ms,
            idempotency_key=None,
        )
        return TaskStatus.from_mapping(_require_body(response))

    def _send_with_retry(
        self,
        *,
        method: str,
        path: str,
        json_body: Mapping[str, Any] | None,
        budget_ms: int,
        idempotency_key: str | None,
    ) -> HttpResponse:
        correlation_id = self._correlation_ids()
        budget = DeadlineBudget.start(budget_ms, self._clock)
        attempts = 0
        while True:
            remaining = budget.remaining_ms(self._clock)
            if remaining <= 0:
                raise DeadlineExceeded("deadline exceeded before the next attempt started")
            headers = {
                "x-correlation-id": correlation_id,
                "x-relay-budget-ms": str(remaining),
            }
            if idempotency_key is not None:
                headers["idempotency-key"] = idempotency_key
            request = HttpRequest(method=method, path=path, headers=headers, json_body=json_body)
            try:
                response = self._transport.send(request)
            except (RequestDroppedError, ReplyDroppedError) as exc:
                decision = decide_retry(request, RetryOutcome.transport_loss())
                if attempts >= self._max_retries or not decision.retry:
                    raise TransportFailure(decision.reason) from exc
                attempts += 1
                continue

            if budget.remaining_ms(self._clock) <= 0:
                raise DeadlineExceeded("deadline exceeded before the caller saw a reply")
            if response.headers.get("x-correlation-id") != correlation_id:
                raise RelayRpcError("correlation ID mismatch in response")
            if response.status_code in RETRYABLE_STATUS_CODES:
                decision = decide_retry(request, RetryOutcome.http_status(response.status_code))
                if attempts >= self._max_retries or not decision.retry:
                    raise RelayRpcError(f"service returned HTTP {response.status_code}")
                attempts += 1
                continue
            if response.status_code == 404:
                message = str((_require_body(response)).get("error", "task not found"))
                raise TaskNotFound(message)
            if response.status_code == 409:
                raise IdempotencyConflict("idempotency key reused with a different request body")
            if response.status_code not in {200, 202}:
                raise RelayRpcError(f"service returned HTTP {response.status_code}")
            return response


def decide_retry(request: HttpRequest, outcome: RetryOutcome) -> RetryDecision:
    """Decide whether a request is safe to retry."""

    safe_to_retry = request.method == "GET" or "idempotency-key" in request.headers
    if outcome.kind is RetryOutcomeKind.TRANSPORT_LOSS:
        if safe_to_retry:
            return RetryDecision(True, "safe to retry after transport loss")
        return RetryDecision(False, "unsafe to retry a mutating request without idempotency")
    if outcome.status_code in RETRYABLE_STATUS_CODES:
        if safe_to_retry:
            return RetryDecision(True, f"safe to retry after HTTP {outcome.status_code}")
        return RetryDecision(False, f"HTTP {outcome.status_code} is retryable only for safe calls")
    return RetryDecision(False, "response is final")


def _request_fingerprint(request: HttpRequest) -> str:
    return json.dumps(
        {
            "method": request.method,
            "path": request.path,
            "json_body": request.json_body,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _require_header(headers: Mapping[str, str], name: str) -> str:
    try:
        value = headers[name]
    except KeyError as exc:
        raise RelayRpcError(f"missing required header: {name}") from exc
    if not value:
        raise RelayRpcError(f"missing required header: {name}")
    return value


def _parse_positive_int(value: str, *, field_name: str) -> int:
    if not value.isdigit() or value == "0":
        raise RelayRpcError(f"{field_name} must be a positive integer")
    return int(value)


def _require_body(response: HttpResponse) -> Mapping[str, Any]:
    if response.json_body is None:
        raise RelayRpcError("response body is missing")
    return response.json_body
