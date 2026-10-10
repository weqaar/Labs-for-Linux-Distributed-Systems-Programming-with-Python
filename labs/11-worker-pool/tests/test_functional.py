"""Functional tests for checkpoint 11.

These tests drive `RelayWorkerPool` through its public methods (`submit`,
`start`, `snapshot`, `list_tasks`, `request_shutdown` and `join`) over an
`InMemoryVisibilityQueue` with a `ManualClock`. Each test runs a whole job
lifecycle, from `queued` through `running` to `succeeded` or `failed`, and
synchronises with the worker threads through events set by the handler rather
than timing sleeps.
"""

# pyright: strict

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest

from lab_11_worker_pool import (
    InMemoryVisibilityQueue,
    ManualClock,
    RelayTask,
    RelayWorkerPool,
    TaskDisposition,
    TaskState,
    WorkerLostError,
)

WAIT = 5.0


@dataclass
class Harness:
    clock: ManualClock
    queue: InMemoryVisibilityQueue[RelayTask]
    pools: list[RelayWorkerPool] = field(default_factory=list[RelayWorkerPool])

    def pool(self, handler: Callable[[RelayTask, int], TaskDisposition]) -> RelayWorkerPool:
        pool = RelayWorkerPool(self.queue, handler, concurrency=1, poll_interval=0.01)
        self.pools.append(pool)
        return pool


@pytest.fixture
def harness() -> Iterator[Harness]:
    clock = ManualClock()
    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=30.0, clock=clock.now)
    created = Harness(clock, queue)
    yield created
    for pool in created.pools:
        pool.request_shutdown()
        assert pool.join(WAIT)


class LifecycleHandler:
    """Succeed, fail and raise by task id; stop the pool on the last job."""

    def __init__(self) -> None:
        self.pool: RelayWorkerPool | None = None
        self.seen_running: dict[str, TaskState] = {}
        self.last_started = threading.Event()

    def __call__(self, task: RelayTask, delivery_count: int) -> TaskDisposition:
        del delivery_count
        if self.pool is None:
            raise RuntimeError("pool not attached")
        self.seen_running[task.task_id] = self.pool.snapshot(task.task_id).state
        if task.task_id == "task-18":
            return TaskDisposition.FAILED
        if task.task_id == "task-19":
            raise OSError("index volume is read-only")
        if task.task_id == "task-20":
            self.pool.request_shutdown()
            self.last_started.set()
        return TaskDisposition.SUCCEEDED


def test_submitted_jobs_run_to_succeeded_or_failed_and_are_acknowledged(
    harness: Harness,
) -> None:
    handler = LifecycleHandler()
    pool = harness.pool(handler)
    handler.pool = pool
    for number in (17, 18, 19, 20):
        pool.submit(RelayTask(f"task-{number}", "rebuild-search-index"))

    assert [snapshot.state for snapshot in pool.list_tasks()] == [TaskState.QUEUED] * 4
    pool.start()
    assert handler.last_started.wait(WAIT)
    assert pool.join(WAIT)

    states = {snapshot.task_id: snapshot.state for snapshot in pool.list_tasks()}
    assert states == {
        "task-17": TaskState.SUCCEEDED,
        "task-18": TaskState.FAILED,
        "task-19": TaskState.FAILED,
        "task-20": TaskState.SUCCEEDED,
    }
    assert set(handler.seen_running.values()) == {TaskState.RUNNING}
    assert harness.queue.pending_count() == 0


class LosesFirstDelivery:
    """Simulate a worker that dies on the first delivery, then succeed."""

    def __init__(self) -> None:
        self.pool: RelayWorkerPool | None = None
        self.lost = threading.Event()
        self.deliveries: list[int] = []

    def __call__(self, task: RelayTask, delivery_count: int) -> TaskDisposition:
        self.deliveries.append(delivery_count)
        if delivery_count == 1:
            self.lost.set()
            raise WorkerLostError(task.task_id)
        if self.pool is None:
            raise RuntimeError("pool not attached")
        self.pool.request_shutdown()
        return TaskDisposition.SUCCEEDED


def test_job_abandoned_by_a_lost_worker_is_redelivered_after_its_lease_expires(
    harness: Harness,
) -> None:
    handler = LosesFirstDelivery()
    pool = harness.pool(handler)
    handler.pool = pool
    pool.submit(RelayTask("task-17", "compact-queue"))
    pool.start()

    assert handler.lost.wait(WAIT)
    assert harness.queue.visible_count() == 0
    assert harness.queue.pending_count() == 1

    harness.clock.advance(31.0)
    assert pool.join(WAIT)

    snapshot = pool.snapshot("task-17")
    assert snapshot.state is TaskState.SUCCEEDED
    assert snapshot.deliveries == 2
    assert handler.deliveries == [1, 2]
    assert harness.queue.pending_count() == 0


class NeverCalled:
    def __call__(self, task: RelayTask, delivery_count: int) -> TaskDisposition:
        raise AssertionError(f"{task.task_id} should not have been handled")


def test_job_submitted_in_a_non_queued_state_is_rejected_and_not_enqueued(
    harness: Harness,
) -> None:
    pool = harness.pool(NeverCalled())

    with pytest.raises(ValueError, match="queued state"):
        pool.submit(RelayTask("task-17", "rebuild-search-index", TaskState.RUNNING))

    assert pool.list_tasks() == []
    assert harness.queue.pending_count() == 0
    with pytest.raises(KeyError):
        pool.snapshot("task-17")
