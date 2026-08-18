"""Tests for the lab_18_quorum_basics package."""

from __future__ import annotations

import pytest

from lab_18_quorum_basics import (
    QuorumRegisterCluster,
    QuorumUnavailable,
    RelayTaskRecord,
    RelayTaskStatus,
    SimulationClock,
    UnknownTask,
    __version__,
    majority_failure_budget,
    majority_quorum,
    quorums_intersect,
)


def make_cluster() -> QuorumRegisterCluster:
    return QuorumRegisterCluster(
        node_ids=("n1", "n2", "n3", "n4", "n5"),
        clock=SimulationClock(),
    )


def test_intersecting_quorums_return_the_latest_task_version() -> None:
    cluster = make_cluster()
    task_key = "relay:task:42"

    cluster.write_task(
        task_key=task_key,
        record=RelayTaskRecord(
            task_id="task-42",
            shard="orders",
            status=RelayTaskStatus.QUEUED,
        ),
        coordinator="n1",
        write_quorum=2,
        preferred_nodes=("n1", "n2"),
    )
    latest = cluster.write_task(
        task_key=task_key,
        record=RelayTaskRecord(
            task_id="task-42",
            shard="orders",
            status=RelayTaskStatus.RUNNING,
            assigned_worker="worker-b",
            attempt=1,
        ),
        coordinator="n5",
        write_quorum=4,
        preferred_nodes=("n2", "n3", "n4", "n5"),
    )

    result = cluster.read_task(
        task_key=task_key,
        coordinator="n1",
        read_quorum=2,
        preferred_nodes=("n1", "n2"),
    )

    assert quorums_intersect(cluster.node_count, read_quorum=2, write_quorum=4)
    assert result.value == latest
    assert result.value.record.assigned_worker == "worker-b"
    assert not result.stale


def test_non_intersecting_quorums_can_return_a_stale_value() -> None:
    cluster = make_cluster()
    task_key = "relay:task:73"
    stale = cluster.write_task(
        task_key=task_key,
        record=RelayTaskRecord(
            task_id="task-73",
            shard="billing",
            status=RelayTaskStatus.QUEUED,
        ),
        coordinator="n1",
        write_quorum=2,
        preferred_nodes=("n1", "n2"),
    )
    latest = cluster.write_task(
        task_key=task_key,
        record=RelayTaskRecord(
            task_id="task-73",
            shard="billing",
            status=RelayTaskStatus.RUNNING,
            assigned_worker="worker-c",
            attempt=1,
        ),
        coordinator="n5",
        write_quorum=2,
        preferred_nodes=("n4", "n5"),
    )

    result = cluster.read_task(
        task_key=task_key,
        coordinator="n1",
        read_quorum=2,
        preferred_nodes=("n1", "n2"),
    )

    assert not quorums_intersect(cluster.node_count, read_quorum=2, write_quorum=2)
    assert result.value == stale
    assert result.value != latest
    assert result.stale


def test_minority_partition_stalls_writes_while_majority_can_progress() -> None:
    cluster = make_cluster()
    task_key = "relay:task:91"
    baseline = cluster.write_task(
        task_key=task_key,
        record=RelayTaskRecord(
            task_id="task-91",
            shard="invoices",
            status=RelayTaskStatus.QUEUED,
        ),
        coordinator="n3",
        write_quorum=3,
        preferred_nodes=("n1", "n2", "n3"),
    )
    cluster.set_partition((("n1", "n2"), ("n3", "n4", "n5")))

    with pytest.raises(QuorumUnavailable, match="needs 3"):
        cluster.write_task(
            task_key=task_key,
            record=RelayTaskRecord(
                task_id="task-91",
                shard="invoices",
                status=RelayTaskStatus.RUNNING,
                assigned_worker="worker-a",
                attempt=1,
            ),
            coordinator="n1",
            write_quorum=3,
        )

    latest = cluster.write_task(
        task_key=task_key,
        record=RelayTaskRecord(
            task_id="task-91",
            shard="invoices",
            status=RelayTaskStatus.RUNNING,
            assigned_worker="worker-b",
            attempt=1,
        ),
        coordinator="n3",
        write_quorum=3,
        preferred_nodes=("n3", "n4", "n5"),
    )
    stale_read = cluster.read_task(
        task_key=task_key,
        coordinator="n1",
        read_quorum=2,
        preferred_nodes=("n1", "n2"),
    )

    assert cluster.can_reach_quorum("n1", quorum_size=2)
    assert not cluster.can_reach_quorum("n1", quorum_size=3)
    assert latest.version > baseline.version
    assert stale_read.value == baseline
    assert stale_read.stale


def test_unknown_task_raises_when_no_contacted_replica_has_seen_it() -> None:
    cluster = make_cluster()

    with pytest.raises(UnknownTask, match="has not been written"):
        cluster.read_task(
            task_key="relay:task:404",
            coordinator="n2",
            read_quorum=2,
            preferred_nodes=("n2", "n3"),
        )


def test_majority_math_shows_why_four_nodes_do_not_help() -> None:
    assert majority_quorum(3) == 2
    assert majority_quorum(4) == 3
    assert majority_failure_budget(3) == 1
    assert majority_failure_budget(4) == 1
    assert quorums_intersect(node_count=5, read_quorum=3, write_quorum=3)


def test_version_is_exposed() -> None:
    assert __version__
