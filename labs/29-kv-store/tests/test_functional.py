"""Functional tests for the key-value store checkpoint.

These tests drive the composed repository the way the job service would use
it: ``FakeCosmosClient`` creates the container from
``CosmosTaskRepository.default_container_model()``, and each test then calls
only the repository's public methods across a whole job lifecycle. The fake
client and ``FixedClock`` replace Azure Cosmos DB and elapsed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from lab_29_kv_store import (
    ConditionalWriteFailedError,
    CosmosTaskRepository,
    FakeCosmosClient,
    FixedClock,
    RelayTaskRecord,
    TaskAlreadyExistsError,
    TaskKey,
    TaskNotFoundError,
    TaskPatch,
    TaskStatus,
)

JOB = TaskKey("tenant-a", "task-17")


@dataclass(frozen=True)
class Deployment:
    """Repository wired to the fake client, plus the clock that drives expiry."""

    repository: CosmosTaskRepository
    clock: FixedClock


@pytest.fixture
def deployment() -> Deployment:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    client = FakeCosmosClient(clock=clock)
    container = client.create_task_container(CosmosTaskRepository.default_container_model())
    return Deployment(repository=CosmosTaskRepository(container), clock=clock)


def test_submitted_job_moves_from_queued_to_succeeded_with_etags(
    deployment: Deployment,
) -> None:
    repository = deployment.repository
    created = repository.create_task(
        RelayTaskRecord(JOB, "Index archive", payload={"action": "index"})
    )
    assert created.task.record.status is TaskStatus.QUEUED

    etag = created.task.etag
    for status in (TaskStatus.RUNNING, TaskStatus.SUCCEEDED):
        updated = repository.patch_task(JOB, TaskPatch(status=status), if_match=etag)
        assert updated.task.etag != etag
        etag = updated.task.etag

    read = repository.get_task(JOB)
    assert read.task.record.status is TaskStatus.SUCCEEDED
    assert read.task.record.payload == {"action": "index"}
    assert read.charge.request_units == 1.0
    tenant_jobs = repository.query_tasks(tenant_id="tenant-a", status=TaskStatus.SUCCEEDED)
    assert [task.record.key for task in tenant_jobs.tasks] == [JOB]
    assert tenant_jobs.charge.cross_partition is False


def test_duplicate_job_id_is_rejected_and_original_is_kept(deployment: Deployment) -> None:
    repository = deployment.repository
    repository.create_task(RelayTaskRecord(JOB, "Original"))

    with pytest.raises(TaskAlreadyExistsError, match="task-17"):
        repository.create_task(RelayTaskRecord(JOB, "Duplicate"))

    assert repository.get_task(JOB).task.record.title == "Original"


def test_concurrent_writer_with_stale_etag_cannot_overwrite_outcome(
    deployment: Deployment,
) -> None:
    repository = deployment.repository
    created = repository.create_task(RelayTaskRecord(JOB, "Index archive"))
    worker_a_etag = created.task.etag
    worker_b_etag = created.task.etag

    repository.patch_task(JOB, TaskPatch(status=TaskStatus.SUCCEEDED), if_match=worker_a_etag)
    with pytest.raises(ConditionalWriteFailedError):
        repository.patch_task(JOB, TaskPatch(status=TaskStatus.FAILED), if_match=worker_b_etag)

    assert repository.get_task(JOB).task.record.status is TaskStatus.SUCCEEDED


def test_finished_job_expires_after_its_time_to_live(deployment: Deployment) -> None:
    repository = deployment.repository
    created = repository.create_task(RelayTaskRecord(JOB, "Short lived"), ttl_seconds=60)
    deployment.clock.advance(seconds=30)
    repository.patch_task(JOB, TaskPatch(status=TaskStatus.FAILED), if_match=created.task.etag)

    deployment.clock.advance(seconds=29)
    assert repository.get_task(JOB).task.record.status is TaskStatus.FAILED

    deployment.clock.advance(seconds=1)
    with pytest.raises(TaskNotFoundError, match="task-17"):
        repository.get_task(JOB)
    assert repository.query_tasks(tenant_id="tenant-a").tasks == ()
