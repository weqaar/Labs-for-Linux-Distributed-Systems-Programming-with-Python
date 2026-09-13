"""Tests for the relay stateless service checkpoint."""

from __future__ import annotations

from threading import Event, Thread
from typing import cast

from fastapi.testclient import TestClient
from httpx import Response

from lab_27_stateless_service import (
    InMemoryTaskRepository,
    TaskStatus,
    create_app,
)
from lab_27_stateless_service.contract import CreateTaskCommand


def test_two_instances_share_one_repository() -> None:
    repository = InMemoryTaskRepository()
    app_a = create_app(repository=repository)
    app_b = create_app(repository=repository)

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        created = client_a.post(
            "/tasks",
            json={
                "tenant_id": "tenant-a",
                "task_id": "relay-001",
                "title": "Import relay task",
                "payload": {"source": "blob://input/a.json"},
                "depends_on": [],
            },
        )

        assert created.status_code == 201
        created_body = created.json()
        assert created_body["status"] == "queued"
        assert "instance" not in created_body

        fetched = client_b.get("/tasks/tenant-a/relay-001")

        assert fetched.status_code == 200
        assert fetched.json() == created_body
        assert fetched.headers["etag"] == created.headers["etag"]

        listed = client_b.get("/tasks", params={"tenant_id": "tenant-a"})

        assert listed.status_code == 200
        assert listed.json() == [created_body]


def test_patch_uses_if_match_optimistic_concurrency() -> None:
    repository = InMemoryTaskRepository()
    app = create_app(repository=repository)

    with TestClient(app) as client:
        created = client.post(
            "/tasks",
            json={
                "tenant_id": "tenant-a",
                "task_id": "relay-002",
                "title": "Patch relay task",
            },
        )
        initial_etag = created.headers["etag"]

        updated = client.patch(
            "/tasks/tenant-a/relay-002",
            headers={"If-Match": initial_etag},
            json={"status": "running"},
        )

        assert updated.status_code == 200
        assert updated.json()["status"] == "running"
        assert updated.headers["etag"] != initial_etag

        conflict = client.patch(
            "/tasks/tenant-a/relay-002",
            headers={"If-Match": initial_etag},
            json={"status": "failed"},
        )

        assert conflict.status_code == 412

        missing_precondition = client.patch(
            "/tasks/tenant-a/relay-002",
            json={"status": "failed"},
        )

        assert missing_precondition.status_code == 428


def test_liveness_and_readiness_are_separate() -> None:
    repository = InMemoryTaskRepository()
    app = create_app(repository=repository)

    with TestClient(app) as client:
        assert client.get("/livez").status_code == 200
        assert client.get("/readyz").status_code == 200

        repository.set_ready(False)

        assert client.get("/livez").status_code == 200
        assert client.get("/readyz").status_code == 503

        repository.set_ready(True)

        drained = client.post("/drain")
        assert drained.status_code == 200
        assert drained.json()["draining"] is True
        assert client.get("/livez").status_code == 200
        assert client.get("/readyz").status_code == 503

        rejected = client.post(
            "/tasks",
            json={
                "tenant_id": "tenant-a",
                "task_id": "relay-003",
                "title": "Should wait for replacement instance",
            },
        )
        assert rejected.status_code == 503


def test_in_flight_request_finishes_after_drain_starts() -> None:
    class BlockingRepository(InMemoryTaskRepository):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.release = Event()

        def create_task(self, command: CreateTaskCommand):  # type: ignore[override]
            self.started.set()
            assert self.release.wait(timeout=2.0)
            return super().create_task(command)

    repository = BlockingRepository()
    app = create_app(repository=repository)

    response_holder: dict[str, object] = {}

    with TestClient(app) as client_worker, TestClient(app) as client_admin:
        worker = Thread(
            target=lambda: response_holder.setdefault(
                "response",
                client_worker.post(
                    "/tasks",
                    json={
                        "tenant_id": "tenant-a",
                        "task_id": "relay-004",
                        "title": "Drain safely",
                        "status": TaskStatus.QUEUED.value,
                    },
                ),
            )
        )
        worker.start()
        assert repository.started.wait(timeout=2.0)

        drain = client_admin.post("/drain")
        assert drain.status_code == 200
        assert drain.json()["draining"] is True

        repository.release.set()
        worker.join(timeout=2.0)
        response = cast(Response, response_holder["response"])
        assert response.status_code == 201

        blocked = client_admin.post(
            "/tasks",
            json={
                "tenant_id": "tenant-a",
                "task_id": "relay-005",
                "title": "Rejected after drain",
            },
        )
        assert blocked.status_code == 503
