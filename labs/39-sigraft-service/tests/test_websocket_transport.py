# SPDX-License-Identifier: Apache-2.0
"""Live loopback transport and deterministic job-event boundary tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect
from websockets.typing import Origin

from lab_39_sigraft_service.job_events import JobEvent, JobSubscription
from lab_39_sigraft_service.sigraft_service import SigRaftService, run_server
from lab_39_sigraft_service.sigraftctl import SigRaftClient, WebSocketClient, main
from lab_39_sigraft_service.websocket_transport import (
    SUBPROTOCOL,
    HostedWebSocketServer,
    decode_message,
    load_credentials,
    run_websocket_server,
)

TOKEN = "a" * 40
READ_TOKEN = "b" * 40
WRITE_TOKEN = "c" * 40


@pytest.fixture
def service() -> Iterator[SigRaftService]:
    instance = SigRaftService(release_digest="sha256:" + "a" * 64)
    try:
        yield instance
    finally:
        instance.shutdown()


@pytest.fixture
def hosted(service: SigRaftService) -> Iterator[HostedWebSocketServer]:
    server = run_websocket_server(
        service,
        credentials={
            TOKEN: frozenset({"tasks:read", "tasks:write"}),
            READ_TOKEN: frozenset({"tasks:read"}),
            WRITE_TOKEN: frozenset({"tasks:write"}),
        },
    )
    try:
        yield server
    finally:
        server.close()


def test_http_and_websocket_share_records(hosted: HostedWebSocketServer) -> None:
    # A separate HTTP listener still accesses exactly the same service object.
    with connect(
        hosted.base_url,
        subprotocols=[SUBPROTOCOL],
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
        proxy=None,
    ) as connection:
        connection.send(
            json.dumps(
                {
                    "id": "1",
                    "operation": "request",
                    "method": "POST",
                    "path": "/tasks",
                    "body": {"action": "count failed requests", "checkpoint": 39},
                }
            )
        )
        response = json.loads(connection.recv(timeout=2))
        assert response["status"] == 202
        assert response["data"]["state"] == "queued"


def test_shared_service_and_graphql(hosted: HostedWebSocketServer, service: SigRaftService) -> None:
    http = run_server(service)
    client = WebSocketClient(hosted.base_url, token=TOKEN)
    try:
        job = client.submit("count failed requests", checkpoint=39)
        assert SigRaftClient(http.base_url).status(job.task_id) == client.status(job.task_id)
        assert client.metadata()["service"] == "sigraft"
        result = client.graphql(
            "query($id: ID!) { task(id: $id) { state } }", variables={"id": job.task_id}
        )
        assert result["data"]["task"]["state"] == "QUEUED"
    finally:
        client.close()
        # hosted fixture owns shutdown of service; avoid shutting it down twice.
        http.server.shutdown()
        http.thread.join(timeout=5)
        http.server.server_close()


def test_watch_snapshot_changes_and_terminal_cleanup(
    hosted: HostedWebSocketServer, service: SigRaftService
) -> None:
    job = service.submit_task("count failed requests")
    client = WebSocketClient(hosted.base_url, token=READ_TOKEN)
    events = client.watch(job.task_id)
    first = next(events)
    service.transition_task(job.task_id, "running")
    second = next(events)
    service.transition_task(job.task_id, "succeeded")
    third = next(events)
    assert [e["job"]["state"] for e in (first, second, third)] == ["queued", "running", "succeeded"]
    assert first["sequence"] < second["sequence"] < third["sequence"]
    assert len({e["epoch"] for e in (first, second, third)}) == 1
    with pytest.raises(StopIteration):
        next(events)


def test_reconnect_reads_current_state_without_claiming_replay(
    hosted: HostedWebSocketServer, service: SigRaftService
) -> None:
    job = service.submit_task("count failed requests")
    first = WebSocketClient(hosted.base_url, token=READ_TOKEN).watch(job.task_id)
    assert next(first)["job"]["state"] == "queued"
    first.close()
    service.transition_task(job.task_id, "running")
    service.transition_task(job.task_id, "failed")
    second = WebSocketClient(hosted.base_url, token=READ_TOKEN).watch(job.task_id)
    assert [event["job"]["state"] for event in second] == ["failed"]


@pytest.mark.parametrize(
    ("token", "operation"),
    [(READ_TOKEN, "submit"), (WRITE_TOKEN, "status"), (WRITE_TOKEN, "watch")],
)
def test_credentials_enforce_operation_scopes(
    hosted: HostedWebSocketServer, token: str, operation: str, service: SigRaftService
) -> None:
    job = service.submit_task("count failed requests")
    client = WebSocketClient(hosted.base_url, token=token)
    try:
        with pytest.raises(ValueError, match="403"):
            if operation == "submit":
                client.submit("count failed requests")
            elif operation == "watch":
                next(client.watch(job.task_id))
            else:
                client.status(job.task_id)
    finally:
        client.close()


def test_caller_scopes_cannot_escalate_graphql(hosted: HostedWebSocketServer) -> None:
    client = WebSocketClient(hosted.base_url, token=READ_TOKEN)
    try:
        result = client.graphql(
            'mutation { submitTask(action: "count failures") { id } }',
            scopes=frozenset({"tasks:write"}),
        )
        assert result["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    finally:
        client.close()


@pytest.mark.parametrize(
    ("path", "headers", "origin", "status"),
    [
        ("/ws", {}, None, 401),
        ("/ws", {"Authorization": "Bearer wrong"}, None, 401),
        ("/else", {"Authorization": f"Bearer {TOKEN}"}, None, 404),
        ("/ws", {"Authorization": f"Bearer {TOKEN}"}, "https://example.org", 403),
    ],
)
def test_handshake_boundaries(
    hosted: HostedWebSocketServer,
    path: str,
    headers: dict[str, str],
    origin: str | None,
    status: int,
) -> None:
    with pytest.raises(InvalidStatus) as error:
        connect(
            hosted.base_url.removesuffix("/ws") + path,
            subprotocols=[SUBPROTOCOL],
            additional_headers=headers,
            origin=Origin(origin) if origin else None,
            proxy=None,
        )
    assert error.value.response.status_code == status


@pytest.mark.parametrize("raw", ["[]", '{"id": ""}', b"binary", "not json"])
def test_invalid_envelope_closes_connection(
    hosted: HostedWebSocketServer, raw: str | bytes
) -> None:
    with connect(
        hosted.base_url,
        subprotocols=[SUBPROTOCOL],
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
        proxy=None,
    ) as connection:
        connection.send(raw)
        with pytest.raises(ConnectionClosed):
            connection.recv(timeout=2)
        assert connection.close_code == 1008


def test_duplicate_id_does_not_repeat_submission(
    hosted: HostedWebSocketServer, service: SigRaftService
) -> None:
    with connect(
        hosted.base_url,
        subprotocols=[SUBPROTOCOL],
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
        proxy=None,
    ) as connection:
        message = json.dumps(
            {
                "id": "same",
                "operation": "request",
                "method": "POST",
                "path": "/tasks",
                "body": {"action": "count failures"},
            }
        )
        connection.send(message)
        assert json.loads(connection.recv(timeout=2))["status"] == 202
        connection.send(message)
        assert json.loads(connection.recv(timeout=2))["status"] == 409
        assert len(service.list_tasks(first=100)) == 1


def test_oversized_message_closes_connection(hosted: HostedWebSocketServer) -> None:
    with connect(
        hosted.base_url,
        subprotocols=[SUBPROTOCOL],
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
        proxy=None,
    ) as connection:
        connection.send("x" * 16385)
        with pytest.raises(ConnectionClosed):
            connection.recv(timeout=2)
        assert connection.close_code == 1009


def test_snapshot_registration_and_transition_are_atomic(service: SigRaftService) -> None:
    job = service.submit_task("count failures")
    subscription = service.subscribe_task(job.task_id)
    service.transition_task(job.task_id, "running")
    assert subscription.events.get_nowait().job["state"] == "queued"
    assert subscription.events.get_nowait().job["state"] == "running"
    service.unsubscribe_task(subscription)
    service.transition_task(job.task_id, "failed")
    assert subscription.events.empty()


def test_mailbox_overflow_is_explicit_and_bounded() -> None:
    subscription = JobSubscription("task-17")
    for n in range(40):
        subscription.publish(JobEvent(n, {"state": "running"}))
    assert subscription.overflow.is_set()
    assert subscription.events.qsize() == 32


def test_subscription_capacity_and_invalid_transitions(service: SigRaftService) -> None:
    job = service.submit_task("count failures")
    subscriptions = [service.subscribe_task(job.task_id) for _ in range(32)]
    with pytest.raises(ValueError, match="capacity"):
        service.subscribe_task(job.task_id)
    for subscription in subscriptions:
        service.unsubscribe_task(subscription)
    with pytest.raises(KeyError):
        service.subscribe_task("task-999")
    with pytest.raises(ValueError, match="invalid transition"):
        service.transition_task(job.task_id, "succeeded")
    service.transition_task(job.task_id, "running")
    service.transition_task(job.task_id, "failed")
    with pytest.raises(ValueError, match="invalid transition"):
        service.transition_task(job.task_id, "running")


def test_shutdown_interrupts_idle_watch(service: SigRaftService) -> None:
    server = run_websocket_server(service, credentials={TOKEN: frozenset({"tasks:read"})})
    job = service.submit_task("count failures")
    events = WebSocketClient(server.base_url, token=TOKEN).watch(job.task_id)
    next(events)
    server.close()
    with pytest.raises(ConnectionClosed):
        next(events)
    assert not server.thread.is_alive()
    assert not service._subscriptions


def test_private_credential_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({TOKEN: ["tasks:read"]}))
    path.chmod(0o600)
    assert load_credentials(path) == {TOKEN: frozenset({"tasks:read"})}
    path.chmod(0o644)
    with pytest.raises(ValueError, match="group"):
        load_credentials(path)
    path.chmod(0o600)
    for value in [{}, {"short": ["tasks:read"]}, {TOKEN: ["administrator"]}, []]:
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            load_credentials(path)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1",
        "ws://example.org",
        "ws://127.0.0.1/a",
        "wss://u:p@example.org",
        "wss://example.org/ws?token=x",
    ],
)
def test_client_rejects_unsafe_or_ambiguous_url(url: str) -> None:
    with pytest.raises(ValueError):
        WebSocketClient(url, token=TOKEN)


def test_cli_transport_and_watch(
    hosted: HostedWebSocketServer,
    service: SigRaftService,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN)
    token_file.chmod(0o600)
    args = [
        "--base-url",
        hosted.base_url,
        "--transport",
        "websocket",
        "--token-file",
        str(token_file),
    ]
    assert main([*args, "submit", "count failures"]) == 0
    job_id = json.loads(capsys.readouterr().out)["task_id"]
    assert main([*args, "status", job_id]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "queued"
    assert main([*args, "metadata"]) == 0
    capsys.readouterr()
    assert main([*args, "graphql", "{ tasks { id } }"]) == 0
    capsys.readouterr()
    service.transition_task(job_id, "running")
    service.transition_task(job_id, "succeeded")
    assert main([*args, "status", job_id, "--watch"]) == 0
    assert json.loads(capsys.readouterr().out)["job"]["state"] == "succeeded"
    assert main([*args, "status", "task-999"]) == 1
    assert "No automatic retry" in capsys.readouterr().err


def test_cli_rejects_incompatible_options() -> None:
    with pytest.raises(SystemExit) as error:
        main(["--base-url", "http://127.0.0.1:8081", "status", "task-1", "--watch"])
    assert error.value.code == 2


def test_connection_still_accepts_commands_while_subscribed(
    hosted: HostedWebSocketServer, service: SigRaftService
) -> None:
    job = service.submit_task("count failures")
    with connect(
        hosted.base_url,
        subprotocols=[SUBPROTOCOL],
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
        proxy=None,
    ) as connection:
        connection.send(json.dumps({"id": "watch", "operation": "watch", "task_id": job.task_id}))
        assert json.loads(connection.recv(timeout=2))["status"] == 200
        assert json.loads(connection.recv(timeout=2))["type"] == "event"
        connection.send(json.dumps({"id": "stop", "operation": "unwatch"}))
        assert json.loads(connection.recv(timeout=2))["data"]["watching"] is False
        connection.send(
            json.dumps(
                {"id": "metadata", "operation": "request", "method": "GET", "path": "/metadata"}
            )
        )
        assert json.loads(connection.recv(timeout=2))["data"]["service"] == "sigraft"


def test_bad_messages_are_rejected_before_dispatch() -> None:
    for value in ["{}", "[]", b"{}", '{"id": true}', '{"id":"' + "x" * 65 + '"}']:
        with pytest.raises(ValueError):
            decode_message(value)
