"""Tests for the relay WebSocket event stream."""

from __future__ import annotations

from lab_16_websocket_service import (
    BearerToken,
    FakeClock,
    FakeConnection,
    InMemoryBroker,
    KeepalivePolicy,
    ReconnectPolicy,
    RelayStreamSession,
    TaskAction,
    TaskState,
    __version__,
)


def test_resume_replays_missed_events_and_streams_new_ones() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.QUEUED, detail="queued"
    )
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.RUNNING, detail="running"
    )

    connection = FakeConnection()
    session = RelayStreamSession(
        broker,
        connection,
        clock,
        BearerToken("relayctl", expires_at_ms=60_000),
        resume_after=1,
    )
    replayed = connection.drain()
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, detail="done"
    )
    live = connection.drain()

    assert [frame["sequence"] for frame in replayed] == [2]
    assert [frame["sequence"] for frame in live] == [3]
    assert session.last_sequence == 3


def test_keepalive_policy_sends_ping_without_sleep() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    connection = FakeConnection()
    session = RelayStreamSession(
        broker,
        connection,
        clock,
        BearerToken("relayctl", expires_at_ms=60_000),
        keepalive=KeepalivePolicy(idle_interval_ms=1_000),
    )

    clock.advance(1_000)
    session.tick()

    assert connection.drain() == [{"type": "ping", "resume_from": 0}]


def test_slow_client_is_closed_when_the_outbound_queue_fills() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    connection = FakeConnection(outbound_limit=1)
    RelayStreamSession(
        broker,
        connection,
        clock,
        BearerToken("relayctl", expires_at_ms=60_000),
    )

    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.QUEUED, detail="queued"
    )
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.RUNNING, detail="running"
    )

    assert connection.closed
    assert connection.close_code == 4002
    assert connection.close_reason == "slow client"


def test_expired_token_closes_the_connection() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    connection = FakeConnection()
    session = RelayStreamSession(
        broker,
        connection,
        clock,
        BearerToken("relayctl", expires_at_ms=500),
        keepalive=KeepalivePolicy(idle_interval_ms=1_000),
    )

    clock.advance(500)
    session.tick()

    assert connection.closed
    assert connection.close_code == 4001
    assert connection.close_reason == "bearer token expired"


def test_broker_fanout_reaches_two_connections() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    first_connection = FakeConnection()
    second_connection = FakeConnection()
    RelayStreamSession(broker, first_connection, clock, BearerToken("a", expires_at_ms=60_000))
    RelayStreamSession(broker, second_connection, clock, BearerToken("b", expires_at_ms=60_000))

    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.QUEUED, detail="queued"
    )

    assert [frame["sequence"] for frame in first_connection.drain()] == [1]
    assert [frame["sequence"] for frame in second_connection.drain()] == [1]


def test_resume_after_restart_loses_no_events() -> None:
    clock = FakeClock()
    broker = InMemoryBroker(clock)
    first_connection = FakeConnection()
    first_session = RelayStreamSession(
        broker,
        first_connection,
        clock,
        BearerToken("relayctl", expires_at_ms=60_000),
    )
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.QUEUED, detail="queued"
    )
    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.RUNNING, detail="running"
    )
    received_before_restart = first_connection.drain()
    first_session.close_for_restart()

    broker.publish(
        task_id="task-17", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, detail="done"
    )
    broker.publish(
        task_id="task-18", action=TaskAction.DELIVER, state=TaskState.QUEUED, detail="queued"
    )

    second_connection = FakeConnection()
    RelayStreamSession(
        broker,
        second_connection,
        clock,
        BearerToken("relayctl", expires_at_ms=60_000),
        resume_after=2,
    )

    assert [frame["sequence"] for frame in received_before_restart] == [1, 2]
    assert [frame["sequence"] for frame in second_connection.drain()] == [3, 4]


def test_reconnect_policy_spreads_clients_with_deterministic_jitter() -> None:
    policy = ReconnectPolicy(base_delay_ms=100, max_delay_ms=800, jitter_spread_ms=50)

    delays = [policy.delay_ms(attempt=2, client_id=f"client-{index}") for index in range(100)]

    assert policy.delay_ms(attempt=2, client_id="client-7") == policy.delay_ms(
        attempt=2,
        client_id="client-7",
    )
    assert len(set(delays)) > 1
    assert min(delays) >= 400
    assert max(delays) <= 450


def test_version_is_exposed() -> None:
    assert __version__
