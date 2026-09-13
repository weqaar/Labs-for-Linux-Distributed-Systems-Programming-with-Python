"""Bounded worker-pool execution for relay tasks."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from enum import Enum

from .models import RelayTask, RelayTaskSnapshot, TaskState
from .queue import VisibilityQueue


class TaskDisposition(str, Enum):
    """Completion states a worker may report."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorkerLostError(RuntimeError):
    """Raised to simulate a worker dying before it can delete the lease."""


TaskHandler = Callable[[RelayTask, int], TaskDisposition]


class RelayWorkerPool:
    """Process queued relay tasks with bounded concurrency and graceful drain."""

    def __init__(
        self,
        queue: VisibilityQueue[RelayTask],
        handler: TaskHandler,
        *,
        concurrency: int,
        poll_interval: float = 0.05,
    ) -> None:
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._queue = queue
        self._handler = handler
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._stop_requested = threading.Event()
        self._threads: list[threading.Thread] = []
        self._snapshots: dict[str, RelayTaskSnapshot] = {}
        self._lock = threading.Lock()

    def submit(self, task: RelayTask) -> None:
        if task.state is not TaskState.QUEUED:
            raise ValueError("queued tasks must enter the worker pool in the queued state")
        self._queue.put(task)
        with self._lock:
            self._snapshots[task.task_id] = RelayTaskSnapshot(
                task_id=task.task_id,
                definition=task.definition,
                state=TaskState.QUEUED,
                deliveries=0,
            )

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("worker pool already started")
        for index in range(self._concurrency):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"relay-worker-{index}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def request_shutdown(self) -> None:
        """Represent SIGTERM without sending a process-wide signal."""

        self._stop_requested.set()

    def join(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        return all(not thread.is_alive() for thread in self._threads)

    def snapshot(self, task_id: str) -> RelayTaskSnapshot:
        with self._lock:
            return self._snapshots[task_id]

    def list_tasks(self) -> list[RelayTaskSnapshot]:
        with self._lock:
            return sorted(self._snapshots.values(), key=lambda snapshot: snapshot.task_id)

    def _worker_loop(self) -> None:
        while True:
            if self._stop_requested.is_set():
                return
            lease = self._queue.reserve(timeout=self._poll_interval)
            if lease is None:
                continue
            task = lease.item
            self._record(task, TaskState.RUNNING, lease.delivery_count)
            try:
                disposition = self._handler(task, lease.delivery_count)
            except WorkerLostError:
                self._record(task, TaskState.QUEUED, lease.delivery_count)
                continue
            except Exception:
                self._queue.acknowledge(lease.lease_id)
                self._record(task, TaskState.FAILED, lease.delivery_count)
                continue
            self._queue.acknowledge(lease.lease_id)
            if disposition is TaskDisposition.SUCCEEDED:
                self._record(task, TaskState.SUCCEEDED, lease.delivery_count)
            elif disposition is TaskDisposition.FAILED:
                self._record(task, TaskState.FAILED, lease.delivery_count)
            else:
                raise RuntimeError(f"unsupported task disposition: {disposition!r}")

    def _record(self, task: RelayTask, state: TaskState, deliveries: int) -> None:
        with self._lock:
            current = self._snapshots[task.task_id]
            self._snapshots[task.task_id] = replace(
                current,
                state=state,
                deliveries=deliveries,
            )
