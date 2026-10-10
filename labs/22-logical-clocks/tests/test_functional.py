"""Functional tests for logical clocks on relay job updates.

These tests drive the package's public interface the way an operator tool
would: parse operator timestamps with ``parse_utc_timestamp``, record and
exchange job updates between ``RelayReplica`` objects, order them with
``logical_order`` and ``wall_clock_order``, serialize them to JSON and render
them with ``display_in_timezone``. Every timestamp is supplied by the test, so
no system clock or network is used.
"""

from __future__ import annotations

import json

import pytest

from lab_22_logical_clocks import (
    ClockRelation,
    ContractError,
    RelayReplica,
    TaskAction,
    TaskState,
    display_in_timezone,
    logical_order,
    parse_utc_timestamp,
    wall_clock_order,
)


def test_job_lifecycle_across_skewed_replicas_is_ordered_by_logical_time() -> None:
    west = RelayReplica("west")
    east = RelayReplica("east")

    queued = west.record_local_update(
        "task-17",
        action=TaskAction.DELIVER,
        state=TaskState.QUEUED,
        detail="accepted from relayctl",
        wall_time=parse_utc_timestamp("2026-08-18T13:00:00+01:00"),
    )
    east.observe_remote_update(queued)
    running = east.record_local_update(
        "task-17",
        action=TaskAction.DELIVER,
        state=TaskState.RUNNING,
        detail="worker started",
        wall_time=parse_utc_timestamp("2026-08-18T11:58:00Z"),
    )
    west.observe_remote_update(running)
    succeeded = west.record_local_update(
        "task-17",
        action=TaskAction.DELIVER,
        state=TaskState.SUCCEEDED,
        detail="delivered",
        wall_time=parse_utc_timestamp("2026-08-18T12:02:00Z"),
    )

    history = west.history("task-17")
    assert history == (queued, running, succeeded)
    assert [update.state for update in wall_clock_order(history)] == [
        TaskState.RUNNING,
        TaskState.QUEUED,
        TaskState.SUCCEEDED,
    ]
    assert [update.state for update in logical_order(history)] == [
        TaskState.QUEUED,
        TaskState.RUNNING,
        TaskState.SUCCEEDED,
    ]
    assert queued.relation_to(running) is ClockRelation.BEFORE
    assert running.relation_to(succeeded) is ClockRelation.BEFORE

    payload = json.loads(succeeded.to_json())
    assert payload == {
        "action": "deliver",
        "actor": "west",
        "detail": "delivered",
        "id": "task-17",
        "lamport": {"counter": 5, "node_id": "west"},
        "state": "succeeded",
        "vector": {"east": 1, "west": 2},
        "wall_time": "2026-08-18T12:02:00Z",
    }
    assert display_in_timezone(succeeded.wall_time, "America/New_York") == (
        "2026-08-18T08:02:00-04:00"
    )


def test_conflicting_outcomes_from_two_replicas_are_reported_as_concurrent() -> None:
    west = RelayReplica("west")
    east = RelayReplica("east")
    queued = west.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.QUEUED,
        detail="queued",
        wall_time=parse_utc_timestamp("2026-08-18T12:00:00Z"),
    )
    east.observe_remote_update(queued)

    succeeded = west.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.SUCCEEDED,
        detail="west finished",
        wall_time=parse_utc_timestamp("2026-08-18T12:05:00Z"),
    )
    failed = east.record_local_update(
        "task-17",
        action=TaskAction.INDEX,
        state=TaskState.FAILED,
        detail="east gave up",
        wall_time=parse_utc_timestamp("2026-08-18T12:05:00Z"),
    )
    west.observe_remote_update(failed)
    east.observe_remote_update(succeeded)

    assert succeeded.relation_to(failed) is ClockRelation.CONCURRENT
    assert failed.relation_to(succeeded) is ClockRelation.CONCURRENT
    assert succeeded.merged_vector(failed).to_mapping() == {"east": 1, "west": 2}
    assert logical_order(west.history("task-17")) == logical_order(east.history("task-17"))


def test_operator_input_without_offset_or_with_a_bad_job_id_is_rejected() -> None:
    replica = RelayReplica("west")
    valid_time = parse_utc_timestamp("2026-08-18T12:00:00Z")

    with pytest.raises(ContractError, match="explicit UTC offset"):
        parse_utc_timestamp("2026-08-18T12:00:00")
    with pytest.raises(ContractError, match="valid ISO 8601"):
        parse_utc_timestamp("2026-02-30T12:00:00Z")
    with pytest.raises(ContractError, match="task-<positive integer>"):
        replica.record_local_update(
            "task-017",
            action=TaskAction.ARCHIVE,
            state=TaskState.QUEUED,
            detail="queued",
            wall_time=valid_time,
        )
    with pytest.raises(ContractError, match="unknown timezone"):
        display_in_timezone(valid_time, "Mars/Olympus_Mons")

    assert replica.history("task-017") == ()
