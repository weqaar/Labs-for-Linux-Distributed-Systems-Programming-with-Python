"""Tests for the relay Cosmos-like key-value store checkpoint."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lab_24_kv_store import (
    ConditionalWriteFailedError,
    CosmosTaskRepository,
    FakeCosmosClient,
    FixedClock,
    RelayTaskRecord,
    TaskKey,
    TaskNotFoundError,
    TaskPatch,
    TaskStatus,
)


def _repository() -> CosmosTaskRepository:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    client = FakeCosmosClient(clock=clock)
    container = client.create_task_container(CosmosTaskRepository.default_container_model())
    repository = CosmosTaskRepository(container)
    repository.clock = clock  # type: ignore[attr-defined]
    return repository


def test_partition_key_and_policies_are_explicit() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    client = FakeCosmosClient(clock=clock)
    container = client.create_task_container(CosmosTaskRepository.default_container_model())
    repository = CosmosTaskRepository(container)

    assert repository.partition_key_for(TaskKey("tenant-a", "relay-001")) == "tenant-a"
    assert container.model.partition_key_path == "/tenant_id"
    assert container.model.ttl_policy.default_ttl_seconds == 86_400
    assert "/status/?" in container.model.indexing_policy.included_paths
    assert "/payload/*" in container.model.indexing_policy.excluded_paths
    assert "status" in repository.rejected_partition_keys


def test_point_read_is_cheaper_than_cross_partition_query() -> None:
    repository = _repository()
    repository.create_task(RelayTaskRecord(TaskKey("tenant-a", "relay-001"), "First relay"))
    repository.create_task(RelayTaskRecord(TaskKey("tenant-b", "relay-002"), "Second relay"))

    point_read = repository.get_task(TaskKey("tenant-a", "relay-001"))
    partition_query = repository.query_tasks(tenant_id="tenant-a")
    cross_partition_query = repository.query_tasks(status=TaskStatus.QUEUED)

    assert point_read.charge.request_units == 1.0
    assert partition_query.charge.request_units == 2.0
    assert cross_partition_query.charge.cross_partition is True
    assert cross_partition_query.charge.request_units > point_read.charge.request_units
    assert cross_partition_query.charge.partitions_touched == 2


def test_conditional_write_uses_etags() -> None:
    repository = _repository()
    created = repository.create_task(
        RelayTaskRecord(TaskKey("tenant-a", "relay-003"), "Relay task")
    )

    updated = repository.patch_task(
        TaskKey("tenant-a", "relay-003"),
        TaskPatch(status=TaskStatus.RUNNING),
        if_match=created.task.etag,
    )

    assert updated.task.record.status is TaskStatus.RUNNING
    assert updated.task.etag != created.task.etag

    with pytest.raises(ConditionalWriteFailedError):
        repository.patch_task(
            TaskKey("tenant-a", "relay-003"),
            TaskPatch(status=TaskStatus.FAILED),
            if_match=created.task.etag,
        )


def test_ttl_expiry_is_enforced_by_fake_client() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    client = FakeCosmosClient(clock=clock)
    container = client.create_task_container(CosmosTaskRepository.default_container_model())
    repository = CosmosTaskRepository(container)

    repository.create_task(
        RelayTaskRecord(TaskKey("tenant-a", "relay-004"), "Expiring relay"),
        ttl_seconds=30,
    )
    clock.advance(seconds=31)

    with pytest.raises(TaskNotFoundError):
        repository.get_task(TaskKey("tenant-a", "relay-004"))


def test_query_results_are_sorted_and_tenant_scoped() -> None:
    repository = _repository()
    repository.create_task(RelayTaskRecord(TaskKey("tenant-a", "relay-010"), "Third relay"))
    repository.create_task(RelayTaskRecord(TaskKey("tenant-a", "relay-002"), "First relay"))
    repository.create_task(RelayTaskRecord(TaskKey("tenant-b", "relay-001"), "Other tenant"))

    query = repository.query_tasks(tenant_id="tenant-a")

    assert [task.record.key.task_id for task in query.tasks] == ["relay-002", "relay-010"]
