"""Tests for the relay RPC layer."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

import pytest

from lab_19_rpc_service import (
    MAX_TARGET_LENGTH,
    MAX_TASK_ID_LENGTH,
    TASKS_COLLECTION_PATH,
    ContractError,
    DeadlineExceeded,
    FakeClock,
    FakeTransport,
    HttpRequest,
    IdempotencyConflict,
    RelayHttpService,
    RelayRpcClient,
    RetryOutcome,
    TaskAction,
    TaskNotFound,
    TaskState,
    TaskSubmission,
    TransportFailure,
    TransportFault,
    __version__,
    decide_retry,
)


def make_client(
    transport: FakeTransport,
    clock: FakeClock,
) -> RelayRpcClient:
    numbers = count(1)
    return RelayRpcClient(
        transport,
        clock,
        correlation_ids=lambda: f"corr-{next(numbers)}",
        max_retries=2,
    )


def make_submission(task_id: str = "task-17") -> TaskSubmission:
    return TaskSubmission(
        id=task_id,
        action=TaskAction.INDEX,
        target="blob://relay/inbox/17",
        submitted_at=datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc),
    )


def test_submit_task_uses_the_shared_tasks_contract_and_correlation_id() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(service, clock)
    client = make_client(transport, clock)

    status = client.submit_task(make_submission(), budget_ms=100, idempotency_key="idem-17")

    assert transport.requests[0].path == TASKS_COLLECTION_PATH
    assert transport.requests[0].headers["x-correlation-id"] == "corr-1"
    assert transport.responses[0].headers["x-correlation-id"] == "corr-1"
    assert status.state is TaskState.QUEUED
    assert service.effects_applied == 1


def test_deadline_budget_is_retried_as_a_smaller_budget_after_delay() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(
        service,
        clock,
        faults=(
            TransportFault(delay_ms=60, drop_reply=True),
            TransportFault(),
        ),
    )
    client = make_client(transport, clock)

    status = client.submit_task(make_submission(), budget_ms=100, idempotency_key="idem-17")

    assert status.state is TaskState.QUEUED
    assert len(transport.requests) == 2
    assert transport.requests[0].headers["x-relay-budget-ms"] == "100"
    assert transport.requests[1].headers["x-relay-budget-ms"] == "40"
    assert service.observed_budgets == [100, 40]
    assert service.effects_applied == 1
    assert service.replayed_requests == 1
    assert transport.responses[-1].headers["x-idempotent-replay"] == "true"


def test_duplicate_delivery_produces_one_effect() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(
        service,
        clock,
        faults=(TransportFault(duplicate_request=True),),
    )
    client = make_client(transport, clock)

    status = client.submit_task(make_submission(), budget_ms=100, idempotency_key="idem-17")

    assert status.id == "task-17"
    assert service.effects_applied == 1
    assert service.replayed_requests == 1
    assert transport.responses[-1].headers["x-idempotent-replay"] == "true"


def test_retry_decision_model_respects_idempotency() -> None:
    post_without_key = HttpRequest("POST", TASKS_COLLECTION_PATH, headers={"x-correlation-id": "c"})
    post_with_key = HttpRequest(
        "POST",
        TASKS_COLLECTION_PATH,
        headers={"x-correlation-id": "c", "idempotency-key": "idem-17"},
    )
    get_request = HttpRequest("GET", "/tasks/task-17", headers={"x-correlation-id": "c"})

    assert not decide_retry(post_without_key, RetryOutcome.transport_loss()).retry
    assert decide_retry(post_with_key, RetryOutcome.transport_loss()).retry
    assert decide_retry(get_request, RetryOutcome.http_status(503)).retry


def test_caller_enforces_the_deadline_before_using_a_late_reply() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(service, clock, faults=(TransportFault(delay_ms=150),))
    client = make_client(transport, clock)

    with pytest.raises(DeadlineExceeded, match="caller saw a reply"):
        client.submit_task(make_submission(), budget_ms=100, idempotency_key="idem-17")

    assert service.effects_applied == 1


def test_get_task_reads_the_latest_status() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(service, clock)
    client = make_client(transport, clock)

    submitted = client.submit_task(make_submission(), budget_ms=100, idempotency_key="idem-17")
    fetched = client.get_task(submitted.id, budget_ms=100)

    assert fetched == submitted


def test_reusing_an_idempotency_key_for_a_new_request_is_rejected() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(service, clock)
    client = make_client(transport, clock)

    client.submit_task(make_submission("task-17"), budget_ms=100, idempotency_key="idem-17")
    with pytest.raises(IdempotencyConflict):
        client.submit_task(make_submission("task-18"), budget_ms=100, idempotency_key="idem-17")


def test_missing_task_raises_a_narrow_error() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(service, clock)
    client = make_client(transport, clock)

    with pytest.raises(TaskNotFound, match="task-404"):
        client.get_task("task-404", budget_ms=100)


def test_post_without_idempotency_does_not_retry_after_transport_loss() -> None:
    clock = FakeClock()
    service = RelayHttpService(clock)
    transport = FakeTransport(service, clock, faults=(TransportFault(drop_request=True),))
    client = make_client(transport, clock)

    with pytest.raises(TransportFailure, match="without idempotency"):
        client._send_with_retry(
            method="POST",
            path=TASKS_COLLECTION_PATH,
            json_body=make_submission().to_mapping(),
            budget_ms=100,
            idempotency_key=None,
        )


def test_version_is_exposed() -> None:
    assert __version__


def test_task_id_over_the_length_bound_is_rejected() -> None:
    oversized = "task-" + "1" * MAX_TASK_ID_LENGTH

    with pytest.raises(ContractError, match="at most"):
        make_submission(task_id=oversized)


def test_target_over_the_length_bound_is_rejected() -> None:
    with pytest.raises(ContractError, match="at most"):
        TaskSubmission(
            id="task-17",
            action=TaskAction.INDEX,
            target="x" * (MAX_TARGET_LENGTH + 1),
            submitted_at=datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc),
        )


def test_a_task_id_at_the_length_bound_is_accepted() -> None:
    at_bound = "task-1" + "0" * (MAX_TASK_ID_LENGTH - 6)
    submission = make_submission(task_id=at_bound)

    assert submission.id == at_bound
