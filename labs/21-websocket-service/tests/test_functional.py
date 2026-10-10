"""Functional tests for the WebSocket and GraphQL checkpoint.

These tests drive the public interface a `relayctl` client would meet. Two of
them open a real `graphql-transport-ws` connection to a loopback server on an
ephemeral port through `graphql_websocket_round_trip`. The others follow a
job's status stream through `RelayStreamSession`, `InMemoryBroker` and the
fake clock across a reconnect, so no test sleeps.
"""

# pyright: strict

from __future__ import annotations

import asyncio
from typing import Any, cast

from lab_21_websocket_service import (
    BearerToken,
    FakeClock,
    FakeConnection,
    InMemoryBroker,
    KeepalivePolicy,
    RelayGraphQL,
    RelayStreamSession,
    TaskAction,
    TaskState,
    graphql_websocket_round_trip,
)

ROUND_TRIP_TIMEOUT_SECONDS = 5.0
READ = frozenset({"tasks:read"})
WRITE = frozenset({"tasks:write"})


def over_websocket(
    api: RelayGraphQL, document: str, scopes: frozenset[str]
) -> tuple[dict[str, object], ...]:
    """Run one operation over a real loopback WebSocket with a bounded wait."""

    async def bounded() -> tuple[dict[str, object], ...]:
        return await asyncio.wait_for(
            graphql_websocket_round_trip(api, document, scopes=scopes),
            timeout=ROUND_TRIP_TIMEOUT_SECONDS,
        )

    return asyncio.run(bounded())


def result_payload(frames: tuple[dict[str, object], ...]) -> dict[str, Any]:
    assert frames[0] == {"type": "connection_ack"}
    assert frames[1]["type"] == "next"
    assert frames[2] == {"type": "complete", "id": "operation-1"}
    return cast(dict[str, Any], frames[1]["payload"])


def test_job_submitted_over_websocket_is_read_back_over_websocket() -> None:
    api = RelayGraphQL()

    submitted = result_payload(
        over_websocket(
            api, 'mutation { submitTask(id: "task-17", action: "index") { id state } }', WRITE
        )
    )
    read_back = result_payload(
        over_websocket(api, '{ task(id: "task-17") { id action state } }', READ)
    )

    assert submitted == {"data": {"submitTask": {"id": "task-17", "state": "QUEUED"}}}
    assert read_back == {"data": {"task": {"id": "task-17", "action": "index", "state": "QUEUED"}}}


def test_websocket_rejects_missing_scope_and_conflicting_duplicate_job() -> None:
    api = RelayGraphQL()
    submit_index = 'mutation { submitTask(id: "task-17", action: "index") { id state } }'

    denied = result_payload(over_websocket(api, submit_index, READ))
    first = result_payload(over_websocket(api, submit_index, WRITE))
    repeated = result_payload(over_websocket(api, submit_index, WRITE))
    conflict = result_payload(
        over_websocket(
            api, 'mutation { submitTask(id: "task-17", action: "archive") { id } }', WRITE
        )
    )

    assert denied["errors"] == [{"message": "missing required scope: tasks:write"}]
    assert first == repeated
    assert conflict["errors"] == [{"message": "task id is already bound to another action"}]
    assert api.execute('{ task(id: "task-17") { action } }', scopes=READ).data == {
        "task": {"action": "index"}
    }


def test_watcher_follows_a_job_to_succeeded_across_a_reconnect() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    token = BearerToken("relayctl", expires_at_ms=120_000)
    first_connection = FakeConnection()
    first_session = RelayStreamSession(
        broker, first_connection, clock, token, keepalive=KeepalivePolicy(idle_interval_ms=1_000)
    )

    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.QUEUED, detail="queued"
    )
    clock.advance(1_000)
    first_session.tick()
    before_restart = first_connection.drain()
    resume_from = first_session.last_sequence
    first_session.close_for_restart()
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.RUNNING, detail="running"
    )
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, detail="done"
    )
    second_connection = FakeConnection()
    RelayStreamSession(broker, second_connection, clock, token, resume_after=resume_from)
    after_restart = second_connection.drain()

    assert before_restart[0]["state"] == "queued"
    assert before_restart[1] == {"type": "ping", "resume_from": 1}
    assert first_connection.close_code == 1001
    assert first_connection.drain() == []
    assert [(frame["sequence"], frame["state"]) for frame in after_restart] == [
        (2, "running"),
        (3, "succeeded"),
    ]


def test_reconnect_with_an_expired_token_is_closed_before_any_replay() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.FAILED, detail="target missing"
    )
    clock.advance(60_000)
    connection = FakeConnection()

    RelayStreamSession(
        broker, connection, clock, BearerToken("relayctl", expires_at_ms=60_000), resume_after=0
    )
    broker.publish(
        task_id="task-18", action=TaskAction.INDEX, state=TaskState.QUEUED, detail="queued"
    )

    assert connection.closed
    assert connection.close_code == 4001
    assert connection.close_reason == "bearer token expired"
    assert connection.drain() == []
