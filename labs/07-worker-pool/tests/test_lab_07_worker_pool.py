"""Tests for the lab_07_worker_pool package."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from lab_07_worker_pool import (
    InMemoryVisibilityQueue,
    ManualClock,
    RelayTask,
    RelayWorkerPool,
    TaskDisposition,
    TaskState,
    WorkerLostError,
    __version__,
)


def wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_relay_task_requires_non_empty_fields() -> None:
    with pytest.raises(ValueError, match="task_id"):
        RelayTask("", "rebuild-index")
    with pytest.raises(ValueError, match="definition"):
        RelayTask("task-07", "   ")


def test_visibility_timeout_redelivers_an_unacknowledged_task() -> None:
    clock = ManualClock()
    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=30.0, clock=clock.now)
    task = RelayTask("task-07", "rebuild-index")

    queue.put(task)
    first_lease = queue.reserve(timeout=0.0)
    assert first_lease is not None
    assert first_lease.delivery_count == 1
    assert queue.visible_count() == 0

    clock.advance(31.0)
    second_lease = queue.reserve(timeout=0.0)
    assert second_lease is not None
    assert second_lease.item == task
    assert second_lease.delivery_count == 2

    queue.acknowledge(second_lease.lease_id)
    assert queue.pending_count() == 0


def test_worker_pool_redelivers_a_lost_task() -> None:
    attempts: list[int] = []

    def handler(task: RelayTask, delivery_count: int) -> TaskDisposition:
        attempts.append(delivery_count)
        if delivery_count == 1:
            raise WorkerLostError(task.task_id)
        return TaskDisposition.SUCCEEDED

    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=0.05)
    pool = RelayWorkerPool(queue, handler, concurrency=1, poll_interval=0.01)
    pool.submit(RelayTask("task-08", "compact-queue"))
    pool.start()

    assert wait_until(lambda: pool.snapshot("task-08").state is TaskState.SUCCEEDED)

    pool.request_shutdown()
    assert pool.join(1.0)
    assert attempts == [1, 2]
    assert pool.snapshot("task-08").deliveries == 2


def test_worker_pool_caps_the_number_of_in_flight_tasks() -> None:
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    current = 0
    peak = 0
    started_count = 0

    def handler(task: RelayTask, delivery_count: int) -> TaskDisposition:
        nonlocal current, peak, started_count
        del task, delivery_count
        with lock:
            current += 1
            peak = max(peak, current)
            started_count += 1
            if started_count == 2:
                started.set()
        try:
            assert release.wait(1.0)
        finally:
            with lock:
                current -= 1
        return TaskDisposition.SUCCEEDED

    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=1.0)
    pool = RelayWorkerPool(queue, handler, concurrency=2, poll_interval=0.01)
    for task_number in range(4):
        pool.submit(RelayTask(f"task-{task_number}", f"job-{task_number}"))
    pool.start()

    assert started.wait(1.0)
    with lock:
        assert peak == 2

    release.set()
    assert wait_until(
        lambda: all(snapshot.state is TaskState.SUCCEEDED for snapshot in pool.list_tasks())
    )
    pool.request_shutdown()
    assert pool.join(1.0)


def test_sigterm_drain_finishes_current_task_without_leasing_a_new_one() -> None:
    first_started = threading.Event()
    finish_first = threading.Event()

    def handler(task: RelayTask, delivery_count: int) -> TaskDisposition:
        del delivery_count
        if task.task_id == "task-drain-1":
            first_started.set()
            assert finish_first.wait(1.0)
        return TaskDisposition.SUCCEEDED

    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=1.0)
    pool = RelayWorkerPool(queue, handler, concurrency=1, poll_interval=0.01)
    pool.submit(RelayTask("task-drain-1", "ship-batch"))
    pool.submit(RelayTask("task-drain-2", "ship-batch"))
    pool.start()

    assert first_started.wait(1.0)
    pool.request_shutdown()
    finish_first.set()

    assert pool.join(1.0)
    assert pool.snapshot("task-drain-1").state is TaskState.SUCCEEDED
    assert pool.snapshot("task-drain-2").state is TaskState.QUEUED
    assert queue.pending_count() == 1
    assert queue.visible_count() == 1


def test_version_is_exposed() -> None:
    assert __version__
