"""Tests for relay logical clocks."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lab_22_logical_clocks import (
    ClockRelation,
    LamportClock,
    RelayReplica,
    TaskAction,
    TaskState,
    VectorClock,
    __version__,
    display_in_timezone,
    logical_order,
    parse_utc_timestamp,
    wall_clock_order,
)
from lab_22_logical_clocks.relay import ContractError


def utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 18, hour, minute, tzinfo=timezone.utc)


def test_lamport_clock_advances_for_local_and_remote_events() -> None:
    first = LamportClock("west")
    second = LamportClock("east")

    sent = first.local_event()
    received = second.observe(sent)

    assert sent.counter == 1
    assert received.counter == 2
    assert received.node_id == "east"


def test_vector_clocks_detect_concurrent_updates_and_merge_them() -> None:
    west = RelayReplica("west")
    east = RelayReplica("east")

    queued = west.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.QUEUED,
        detail="queued",
        wall_time=utc(12, 0),
    )
    east.observe_remote_update(queued)
    running = west.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.RUNNING,
        detail="running",
        wall_time=utc(12, 1),
    )
    failed = east.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.FAILED,
        detail="failed elsewhere",
        wall_time=utc(12, 1),
    )

    assert running.relation_to(failed) is ClockRelation.CONCURRENT
    assert running.merged_vector(failed).to_mapping() == {"east": 1, "west": 2}


def test_wall_clock_skew_can_give_the_wrong_order() -> None:
    fast = RelayReplica("fast")
    slow = RelayReplica("slow")

    running = fast.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.RUNNING,
        detail="started",
        wall_time=utc(12, 5),
    )
    slow.observe_remote_update(running)
    finished = slow.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.SUCCEEDED,
        detail="finished",
        wall_time=utc(11, 56),
    )

    assert [update.state for update in wall_clock_order((running, finished))] == [
        TaskState.SUCCEEDED,
        TaskState.RUNNING,
    ]
    assert running.relation_to(finished) is ClockRelation.BEFORE
    assert [update.state for update in logical_order((running, finished))] == [
        TaskState.RUNNING,
        TaskState.SUCCEEDED,
    ]


def test_update_serialization_is_deterministic() -> None:
    replica = RelayReplica("west")
    queued = replica.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.QUEUED,
        detail="queued",
        wall_time=utc(12, 0),
    )
    replica.observe_remote_update(
        queued.__class__(
            id="task-17",
            action=TaskAction.INDEX,
            state=TaskState.QUEUED,
            actor="east",
            detail="observed",
            wall_time=utc(12, 0),
            lamport=queued.lamport,
            vector=VectorClock.from_mapping({"east": 1, "west": 1}),
        )
    )
    merged = replica.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.RUNNING,
        detail="running",
        wall_time=utc(12, 2),
    )

    payload = merged.to_json()

    assert payload == merged.to_json()
    assert '"vector":{"east":1,"west":2}' in payload


def test_replica_history_tracks_observed_updates() -> None:
    west = RelayReplica("west")
    east = RelayReplica("east")

    queued = west.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.QUEUED,
        detail="queued",
        wall_time=utc(12, 0),
    )
    east.observe_remote_update(queued)

    assert east.history("task-17") == (queued,)


def test_pendulum_normalizes_an_explicit_offset_for_the_task_record() -> None:
    parsed = parse_utc_timestamp("2026-08-18T13:30:00+01:00")

    assert parsed.isoformat() == "2026-08-18T12:30:00+00:00"
    assert display_in_timezone(parsed, "Asia/Karachi") == "2026-08-18T17:30:00+05:00"


def test_pendulum_boundary_rejects_implicit_timezones() -> None:
    with pytest.raises(ContractError, match="explicit UTC offset"):
        parse_utc_timestamp("2026-08-18T12:30:00")


def test_version_is_exposed() -> None:
    assert __version__
