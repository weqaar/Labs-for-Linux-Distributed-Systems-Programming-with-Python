"""Functional tests for the relay partitioned store.

These tests drive the public ``ReplicatedTaskStore`` through a whole job
lifecycle: write a queued job, move it to running and then to succeeded,
replicate, and read it back from every replica slice.
"""

from __future__ import annotations

import pytest

from lab_28_partitioned_store import (
    QuorumConfig,
    RelayTaskRecord,
    ReplicatedTaskStore,
    TaskKey,
    TaskNotFoundError,
    TaskStatus,
    UnknownVersionError,
)

NODES = ["node-a", "node-b", "node-c", "node-d", "node-e"]
KEY = TaskKey("tenant-a", "task-17")


def strong_store() -> ReplicatedTaskStore:
    return ReplicatedTaskStore(NODES, QuorumConfig(replica_count=3, read_quorum=2, write_quorum=2))


def test_job_moves_from_queued_to_succeeded_and_every_replica_agrees() -> None:
    store = strong_store()

    queued = store.write_task(RelayTaskRecord(key=KEY, title="Ship release"))
    running = store.write_task(
        RelayTaskRecord(key=KEY, title="Ship release", status=TaskStatus.RUNNING),
        observed_versions=[queued.version.version_id],
    )
    succeeded = store.write_task(
        RelayTaskRecord(key=KEY, title="Ship release", status=TaskStatus.SUCCEEDED),
        observed_versions=[running.version.version_id],
    )

    assert len(queued.owners) == 3
    assert set(queued.owners) <= set(NODES)
    assert len(succeeded.acknowledged_by) == 2
    for offset in range(3):
        result = store.read_task(KEY, replica_offset=offset)
        assert [version.record.status for version in result.versions] == [TaskStatus.SUCCEEDED]

    assert store.replicate_pending() == 3
    assert store.pending_replication_count() == 0
    heads = store.read_all_replicas(KEY)
    assert [version.version_id for version in heads] == [succeeded.version.version_id]


def test_failed_job_written_by_two_workers_is_reported_as_conflict_until_merged() -> None:
    store = strong_store()
    queued = store.write_task(RelayTaskRecord(key=KEY, title="Ship release"))
    store.replicate_pending()

    store.write_task(
        RelayTaskRecord(key=KEY, title="Ship release", status=TaskStatus.SUCCEEDED),
        observed_versions=[queued.version.version_id],
    )
    store.write_task(
        RelayTaskRecord(key=KEY, title="Ship release", status=TaskStatus.FAILED),
        observed_versions=[queued.version.version_id],
    )
    store.replicate_pending()

    conflict = store.read_task(KEY)
    assert conflict.is_concurrent is True
    assert {version.record.status for version in conflict.versions} == {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
    }

    store.write_task(
        RelayTaskRecord(key=KEY, title="Ship release", status=TaskStatus.FAILED),
        observed_versions=[version.version_id for version in conflict.versions],
    )
    resolved = store.read_task(KEY)
    assert resolved.is_concurrent is False
    assert resolved.versions[0].record.status is TaskStatus.FAILED


def test_unknown_job_and_unknown_observed_version_are_rejected() -> None:
    store = strong_store()

    with pytest.raises(TaskNotFoundError, match="tenant-a:task-17"):
        store.read_task(KEY)
    with pytest.raises(UnknownVersionError, match="v99999999"):
        store.write_task(
            RelayTaskRecord(key=KEY, title="Ship release", status=TaskStatus.RUNNING),
            observed_versions=["v99999999"],
        )
    with pytest.raises(TaskNotFoundError):
        store.read_task(KEY)
