"""Unit tests for the in-memory relay task repository used by the HTTP app."""

from __future__ import annotations

import pytest

from lab_27_stateless_service import (
    ConcurrencyConflictError,
    CreateTaskCommand,
    InMemoryTaskRepository,
    PatchTaskCommand,
    RepositoryUnavailableError,
    TaskAlreadyExistsError,
    TaskKey,
    TaskNotFoundError,
    TaskStatus,
)

KEY = TaskKey("tenant-a", "task-17")


def test_duplicate_create_is_rejected() -> None:
    repository = InMemoryTaskRepository()
    repository.create_task(CreateTaskCommand(key=KEY, title="Import jobs"))

    with pytest.raises(TaskAlreadyExistsError, match="tenant-a:task-17"):
        repository.create_task(CreateTaskCommand(key=KEY, title="Import jobs again"))


def test_update_changes_the_etag_and_rejects_a_stale_one() -> None:
    repository = InMemoryTaskRepository()
    created = repository.create_task(CreateTaskCommand(key=KEY, title="Import jobs"))

    updated = repository.update_task(
        KEY, PatchTaskCommand(status=TaskStatus.RUNNING), if_match=created.etag
    )

    assert updated.status is TaskStatus.RUNNING
    assert updated.title == "Import jobs"
    assert updated.etag != created.etag
    with pytest.raises(ConcurrencyConflictError):
        repository.update_task(
            KEY, PatchTaskCommand(status=TaskStatus.FAILED), if_match=created.etag
        )


def test_list_filters_by_tenant_in_key_order() -> None:
    repository = InMemoryTaskRepository()
    for tenant_id, task_id in [
        ("tenant-b", "task-1"),
        ("tenant-a", "task-9"),
        ("tenant-a", "task-2"),
    ]:
        repository.create_task(CreateTaskCommand(key=TaskKey(tenant_id, task_id), title=task_id))

    listed = repository.list_tasks("tenant-a")

    assert [task.key.task_id for task in listed] == ["task-2", "task-9"]


def test_missing_task_and_unready_repository_raise_specific_errors() -> None:
    repository = InMemoryTaskRepository()

    with pytest.raises(TaskNotFoundError):
        repository.get_task(KEY)
    repository.set_ready(False)
    assert repository.is_ready() is False
    with pytest.raises(RepositoryUnavailableError):
        repository.list_tasks()
