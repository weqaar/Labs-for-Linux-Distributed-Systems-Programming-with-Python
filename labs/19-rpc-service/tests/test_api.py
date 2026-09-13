"""Tests for the FastAPI ASGI surface of the relay `/tasks` contract.

``TestClient`` drives the application in process over the same scope, receive
and send interface a real Uvicorn worker would use. No socket is opened and
no uvloop installation is required, so these tests stay fast and offline
while still exercising the real ASGI boundary rather than a fake transport.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from lab_19_rpc_service import (
    MAX_TARGET_LENGTH,
    MAX_TASK_ID_LENGTH,
    MAX_TIMESTAMP_LENGTH,
    RelayTaskState,
    app_factory,
    create_app,
)
from lab_19_rpc_service.api import MAX_BUDGET_DIGITS, MAX_BUDGET_MS, MAX_HEADER_VALUE_LENGTH


def submission_body(task_id: str = "task-19") -> dict[str, str]:
    return {
        "id": task_id,
        "action": "index",
        "target": "blob://relay/inbox/19",
        "submitted_at": "2026-08-18T05:52:44Z",
    }


def test_submit_task_returns_202_with_the_shared_contract_shape() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 202
    assert response.headers["x-correlation-id"] == "corr-1"
    body = response.json()
    assert body["id"] == "task-19"
    assert body["state"] == "queued"
    assert body["submitted_at"] == "2026-08-18T05:52:44Z"


def test_get_task_reads_back_what_was_submitted() -> None:
    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/tasks",
            json=submission_body(),
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )
        response = client.get(
            "/tasks/task-19",
            headers={"x-correlation-id": "corr-2", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "corr-2"
    assert response.json()["state"] == "queued"


def test_missing_task_returns_404() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get(
            "/tasks/task-404",
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 404
    assert "task-404" in response.json()["error"]


def test_missing_correlation_id_is_rejected_before_the_route_runs() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={"x-relay-budget-ms": "100"},
        )

    assert response.status_code == 400


def test_missing_budget_header_is_rejected_before_the_route_runs() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={"x-correlation-id": "corr-1"},
        )

    assert response.status_code == 400


def test_pydantic_validation_rejects_an_unknown_action_before_domain_code_runs() -> None:
    app = create_app()
    body = submission_body()
    body["action"] = "not-a-real-action"
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=body,
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    # Pydantic rejects an action outside the enum as a shape problem: 422, not
    # the domain-level 400 that ContractError would raise for the same field.
    assert response.status_code == 422


def test_domain_validation_rejects_a_task_id_pydantic_would_accept() -> None:
    app = create_app()
    body = submission_body(task_id="not-a-task-id")
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=body,
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    # "not-a-task-id" is a perfectly good string, so Pydantic accepts it.
    # The task-<positive integer> pattern is a domain rule, so it surfaces as
    # the same 400 the fake transport's service would raise.
    assert response.status_code == 400


def test_replaying_an_idempotency_key_returns_the_cached_reply_without_a_new_effect() -> None:
    state = RelayTaskState()
    app = create_app(state)
    with TestClient(app) as client:
        headers = {
            "x-correlation-id": "corr-1",
            "x-relay-budget-ms": "100",
            "idempotency-key": "idem-19",
        }
        first = client.post("/tasks", json=submission_body(), headers=headers)
        second = client.post("/tasks", json=submission_body(), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert second.headers["x-idempotent-replay"] == "true"
    assert state.effects_applied == 1
    assert state.replayed_requests == 1
    assert state.observed_budgets == [100, 100]


def test_reusing_an_idempotency_key_for_a_different_body_is_a_conflict() -> None:
    app = create_app()
    headers = {
        "x-correlation-id": "corr-1",
        "x-relay-budget-ms": "100",
        "idempotency-key": "idem-19",
    }
    with TestClient(app) as client:
        client.post("/tasks", json=submission_body("task-19"), headers=headers)
        response = client.post("/tasks", json=submission_body("task-20"), headers=headers)

    assert response.status_code == 409


def test_two_app_instances_do_not_share_state() -> None:
    app_a = create_app()
    app_b = create_app()
    headers = {"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"}

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        client_a.post("/tasks", json=submission_body(), headers=headers)
        response = client_b.get("/tasks/task-19", headers=headers)

    assert response.status_code == 404


def test_submission_body_round_trips_a_utc_timestamp() -> None:
    assert submission_body()["submitted_at"].endswith("Z")
    parsed = datetime.fromisoformat(submission_body()["submitted_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def test_app_factory_builds_a_working_application() -> None:
    app = app_factory()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 202


def test_app_factory_returns_independent_state_each_call() -> None:
    first = app_factory()
    second = app_factory()
    headers = {"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"}

    with TestClient(first) as client_a, TestClient(second) as client_b:
        client_a.post("/tasks", json=submission_body(), headers=headers)
        response = client_b.get("/tasks/task-19", headers=headers)

    assert response.status_code == 404


def test_oversized_correlation_id_is_rejected_not_a_500() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={
                "x-correlation-id": "c" * (MAX_HEADER_VALUE_LENGTH + 1),
                "x-relay-budget-ms": "100",
            },
        )

    assert response.status_code == 400


def test_oversized_idempotency_key_is_rejected_not_a_500() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={
                "x-correlation-id": "corr-1",
                "x-relay-budget-ms": "100",
                "idempotency-key": "k" * (MAX_HEADER_VALUE_LENGTH + 1),
            },
        )

    assert response.status_code == 400


def test_budget_with_too_many_digits_is_rejected_not_parsed() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={
                "x-correlation-id": "corr-1",
                "x-relay-budget-ms": "1" * (MAX_BUDGET_DIGITS + 1),
            },
        )

    assert response.status_code == 400


def test_budget_over_the_value_ceiling_is_rejected() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={
                "x-correlation-id": "corr-1",
                "x-relay-budget-ms": str(MAX_BUDGET_MS + 1),
            },
        )

    assert response.status_code == 400


def test_budget_at_the_value_ceiling_is_accepted() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={
                "x-correlation-id": "corr-1",
                "x-relay-budget-ms": str(MAX_BUDGET_MS),
            },
        )

    assert response.status_code == 202


def test_oversized_task_id_in_the_body_is_a_422_not_a_500() -> None:
    app = create_app()
    body = submission_body(task_id="task-" + "1" * MAX_TASK_ID_LENGTH)
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=body,
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 422


def test_oversized_target_in_the_body_is_a_422_not_a_500() -> None:
    app = create_app()
    body = submission_body()
    body["target"] = "x" * (MAX_TARGET_LENGTH + 1)
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=body,
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 422


def test_oversized_task_id_in_the_path_is_rejected_not_a_500() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get(
            f"/tasks/{'t' * (MAX_TASK_ID_LENGTH + 1)}",
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 422


def test_oversized_submitted_at_in_the_body_is_a_422_not_a_500() -> None:
    # Pydantic parses ``submitted_at`` straight to ``datetime``, so an
    # oversized digit-free string cannot be caught by the shared contract's
    # MAX_TIMESTAMP_LENGTH bound after the fact: the "before" validator on
    # TaskSubmissionBody has to reject it ahead of that parse.
    app = create_app()
    body = submission_body()
    body["submitted_at"] = "2026-08-18T05:52:44+00:00" + "0" * MAX_TIMESTAMP_LENGTH
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=body,
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 422


def test_submitted_at_at_the_length_bound_is_accepted() -> None:
    app = create_app()
    body = submission_body()
    # A real RFC3339 timestamp is far shorter than the bound; pad the
    # fractional seconds so the string lands exactly at the ceiling, which
    # checks the bound itself accepts a timestamp at the edge rather than
    # only ones comfortably below it.
    prefix, suffix = "2026-08-18T05:52:44.", "Z"
    padding = "0" * (MAX_TIMESTAMP_LENGTH - len(prefix) - len(suffix))
    body["submitted_at"] = f"{prefix}{padding}{suffix}"
    assert len(body["submitted_at"]) == MAX_TIMESTAMP_LENGTH
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=body,
            headers={"x-correlation-id": "corr-1", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 202


def test_blank_correlation_id_is_rejected_like_a_missing_one() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={"x-correlation-id": "   ", "x-relay-budget-ms": "100"},
        )

    assert response.status_code == 400


def test_blank_idempotency_key_is_rejected_rather_than_stored() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tasks",
            json=submission_body(),
            headers={
                "x-correlation-id": "corr-1",
                "x-relay-budget-ms": "100",
                "idempotency-key": "   ",
            },
        )

    assert response.status_code == 400
