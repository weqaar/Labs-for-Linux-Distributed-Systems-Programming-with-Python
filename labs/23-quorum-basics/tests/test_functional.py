"""Functional tests for the quorum register.

These tests drive a five-replica ``QuorumRegisterCluster`` through its public
interface: write job status records with a chosen write quorum, read them back
with a chosen read quorum, partition and heal the simulated network, and
inspect each replica. A ``SimulationClock`` assigns versions, so no wall clock
or real network is used.
"""

from __future__ import annotations

from itertools import combinations

import pytest

from lab_23_quorum_basics import (
    QuorumRegisterCluster,
    QuorumUnavailable,
    RelayTaskRecord,
    RelayTaskStatus,
    SimulationClock,
    UnknownTask,
    majority_quorum,
)

NODES = ("n1", "n2", "n3", "n4", "n5")
TASK_KEY = "relay:task:17"


def record(status: RelayTaskStatus, worker: str | None = None) -> RelayTaskRecord:
    return RelayTaskRecord(
        task_id="task-17",
        shard="default",
        status=status,
        assigned_worker=worker,
        attempt=0 if worker is None else 1,
    )


def test_every_majority_read_returns_the_latest_job_status_through_its_lifecycle() -> None:
    cluster = QuorumRegisterCluster(node_ids=NODES, clock=SimulationClock())
    quorum = majority_quorum(cluster.node_count)
    steps = (
        ("n1", ("n1", "n2", "n3"), record(RelayTaskStatus.QUEUED)),
        ("n3", ("n3", "n4", "n5"), record(RelayTaskStatus.RUNNING, "worker-a")),
        ("n5", ("n1", "n4", "n5"), record(RelayTaskStatus.COMPLETED, "worker-a")),
    )

    for coordinator, write_set, status_record in steps:
        written = cluster.write_task(
            task_key=TASK_KEY,
            record=status_record,
            coordinator=coordinator,
            write_quorum=quorum,
            preferred_nodes=write_set,
        )
        for read_set in combinations(NODES, quorum):
            result = cluster.read_task(
                task_key=TASK_KEY,
                coordinator=read_set[0],
                read_quorum=quorum,
                preferred_nodes=read_set,
            )
            assert result.value == written, read_set
            assert not result.stale

    final = cluster.latest_task(TASK_KEY)
    assert final is not None
    assert final.record.status is RelayTaskStatus.COMPLETED
    assert final.version.tick == 3


def test_partition_refuses_minority_writes_and_heal_restores_the_latest_read() -> None:
    cluster = QuorumRegisterCluster(node_ids=NODES, clock=SimulationClock())
    queued = cluster.write_task(
        task_key=TASK_KEY,
        record=record(RelayTaskStatus.QUEUED),
        coordinator="n1",
        write_quorum=3,
    )
    cluster.set_partition((("n1", "n2"), ("n3", "n4", "n5")))

    with pytest.raises(QuorumUnavailable, match="n1 can reach 2 replicas, needs 3"):
        cluster.write_task(
            task_key=TASK_KEY,
            record=record(RelayTaskStatus.RUNNING, "worker-minority"),
            coordinator="n1",
            write_quorum=3,
        )
    assert cluster.replica_value("n1", TASK_KEY) == queued
    assert cluster.replica_value("n2", TASK_KEY) == queued

    running = cluster.write_task(
        task_key=TASK_KEY,
        record=record(RelayTaskStatus.RUNNING, "worker-majority"),
        coordinator="n4",
        write_quorum=3,
        preferred_nodes=("n3", "n4", "n5"),
    )
    with pytest.raises(QuorumUnavailable, match="needs 3"):
        cluster.read_task(task_key=TASK_KEY, coordinator="n2", read_quorum=3)

    cluster.heal()
    result = cluster.read_task(
        task_key=TASK_KEY,
        coordinator="n1",
        read_quorum=3,
        preferred_nodes=("n1", "n2", "n3"),
    )

    assert result.value == running
    assert result.value.record.assigned_worker == "worker-majority"
    assert not result.stale


def test_reading_a_job_that_was_never_written_is_rejected() -> None:
    cluster = QuorumRegisterCluster(node_ids=NODES, clock=SimulationClock())

    with pytest.raises(UnknownTask, match="relay:task:404 has not been written"):
        cluster.read_task(task_key="relay:task:404", coordinator="n1", read_quorum=3)
    with pytest.raises(ValueError, match="exactly the requested quorum"):
        cluster.read_task(
            task_key=TASK_KEY,
            coordinator="n1",
            read_quorum=3,
            preferred_nodes=("n1", "n2"),
        )

    assert cluster.latest_task("relay:task:404") is None
