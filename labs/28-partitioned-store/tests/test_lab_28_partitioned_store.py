"""Tests for the relay partitioned store checkpoint."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from lab_28_partitioned_store import (
    ConsistentHashRing,
    QuorumConfig,
    RelayTaskRecord,
    ReplicatedTaskStore,
    TaskKey,
    TaskStatus,
)


def test_distribution_and_movement_measurements_are_stable() -> None:
    keys = [
        TaskKey(tenant_id=f"tenant-{index % 17}", task_id=f"relay-{index:05d}")
        for index in range(12000)
    ]
    ring = ConsistentHashRing([f"node-{index}" for index in range(5)], virtual_nodes=512)
    distribution = ring.measure_distribution(keys)

    assert distribution.max_deviation_ratio <= 0.1

    expanded_ring = ConsistentHashRing([f"node-{index}" for index in range(6)], virtual_nodes=512)
    added_movement = ring.measure_movement(keys, expanded_ring)
    assert 0.12 <= added_movement.movement_ratio <= 0.22

    reduced_ring = ConsistentHashRing([f"node-{index}" for index in range(4)], virtual_nodes=512)
    removed_movement = ring.measure_movement(keys, reduced_ring)
    assert 0.18 <= removed_movement.movement_ratio <= 0.35


def test_r_plus_w_greater_than_n_returns_latest_visible_version() -> None:
    store = ReplicatedTaskStore(
        ["node-a", "node-b", "node-c"],
        QuorumConfig(replica_count=3, read_quorum=2, write_quorum=2),
    )
    key = TaskKey("tenant-a", "relay-001")
    base = store.write_task(RelayTaskRecord(key=key, title="Queued relay"))
    store.replicate_pending()

    store.write_task(
        RelayTaskRecord(key=key, title="Running relay", status=TaskStatus.RUNNING),
        observed_versions=[base.version.version_id],
    )

    result = store.read_task(key, replica_offset=1)

    assert len(result.versions) == 1
    assert result.versions[0].record.status is TaskStatus.RUNNING


def test_weaker_quorum_can_return_a_stale_value() -> None:
    store = ReplicatedTaskStore(
        ["node-a", "node-b", "node-c"],
        QuorumConfig(replica_count=3, read_quorum=1, write_quorum=1),
    )
    key = TaskKey("tenant-a", "relay-002")
    base = store.write_task(RelayTaskRecord(key=key, title="Queued relay"))
    store.replicate_pending()

    store.write_task(
        RelayTaskRecord(key=key, title="Running relay", status=TaskStatus.RUNNING),
        observed_versions=[base.version.version_id],
    )

    result = store.read_task(key, replica_offset=1)

    assert len(result.versions) == 1
    assert result.versions[0].record.status is TaskStatus.QUEUED


def test_concurrent_writes_are_surfaced_as_siblings() -> None:
    store = ReplicatedTaskStore(
        ["node-a", "node-b", "node-c"],
        QuorumConfig(replica_count=3, read_quorum=3, write_quorum=3),
    )
    key = TaskKey("tenant-a", "relay-003")
    base = store.write_task(RelayTaskRecord(key=key, title="Queued relay"))

    first = store.write_task(
        RelayTaskRecord(key=key, title="Run branch A", status=TaskStatus.RUNNING),
        observed_versions=[base.version.version_id],
    )
    second = store.write_task(
        RelayTaskRecord(key=key, title="Run branch B", status=TaskStatus.FAILED),
        observed_versions=[base.version.version_id],
    )

    concurrent = store.read_task(key)

    assert concurrent.is_concurrent is True
    assert [version.record.title for version in concurrent.versions] == [
        "Run branch A",
        "Run branch B",
    ]

    store.write_task(
        RelayTaskRecord(key=key, title="Merged branch", status=TaskStatus.RUNNING),
        observed_versions=[first.version.version_id, second.version.version_id],
    )

    merged = store.read_task(key)

    assert merged.is_concurrent is False
    assert merged.versions[0].record.title == "Merged branch"


def test_ring_answers_match_in_a_second_process() -> None:
    keys = [TaskKey("tenant-a", f"relay-{index:03d}") for index in range(10)]
    ring = ConsistentHashRing(["node-a", "node-b", "node-c", "node-d", "node-e"], virtual_nodes=256)
    expected = {key.task_id: ring.owners_for_key(key, 3) for key in keys}

    code = """
import json
from lab_28_partitioned_store import ConsistentHashRing, TaskKey

keys = [TaskKey("tenant-a", f"relay-{index:03d}") for index in range(10)]
ring = ConsistentHashRing(["node-a", "node-b", "node-c", "node-d", "node-e"], virtual_nodes=256)
print(json.dumps({key.task_id: ring.owners_for_key(key, 3) for key in keys}, sort_keys=True))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
    )

    assert json.loads(completed.stdout) == {key: list(value) for key, value in expected.items()}
