"""Spawn-safe multiprocessing examples for CPU-bound relay work."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from typing import Protocol


class LockProxy(Protocol):
    """Lock operations supplied by a multiprocessing manager proxy."""

    def acquire(self) -> bool: ...

    def release(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CpuWork:
    """Small picklable description of CPU work."""

    task_id: str
    seed: int
    rounds: int = 10_000

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if self.rounds < 1:
            raise ValueError("rounds must be positive")


@dataclass(frozen=True, slots=True)
class CpuResult:
    """Result returned across a multiprocessing queue."""

    task_id: str
    value: int
    worker_pid: int


@dataclass(frozen=True, slots=True)
class ProcessRun:
    """Evidence from one bounded process-queue run."""

    start_method: str
    parent_pid: int
    started_worker_pids: tuple[int, ...]
    results: tuple[CpuResult, ...]


@dataclass(frozen=True, slots=True)
class SharedMemoryRun:
    """Evidence from processing slices of one shared-memory segment."""

    byte_sum: int
    worker_pids: tuple[int, ...]
    segment_size: int


def cpu_transform(seed: int, rounds: int) -> int:
    """Perform deterministic CPU work without external state."""

    value = seed & 0xFFFFFFFF
    for _ in range(rounds):
        value = (value * 1_664_525 + 1_013_904_223) & 0xFFFFFFFF
    return value


def run_process_queue(
    work: Iterable[CpuWork],
    *,
    workers: int,
    start_method: str = "spawn",
) -> ProcessRun:
    """Execute bounded CPU work in child interpreters connected by queues."""

    if workers < 1:
        raise ValueError("workers must be positive")
    items = tuple(work)
    if not items:
        return ProcessRun(start_method, os.getpid(), (), ())
    context = mp.get_context(start_method)
    input_queue: Queue[CpuWork | None] = context.Queue(maxsize=max(1, workers * 2))
    output_queue: Queue[CpuResult] = context.Queue()
    processes = [
        context.Process(  # pyright: ignore[reportAttributeAccessIssue]
            target=_queue_worker,
            args=(input_queue, output_queue),
            name=f"relay-process-{index}",
        )
        for index in range(min(workers, len(items)))
    ]
    try:
        for process in processes:
            process.start()
        for item in items:
            input_queue.put(item)
        for _ in processes:
            input_queue.put(None)
        results = tuple(output_queue.get(timeout=10.0) for _ in items)
        _join_or_raise(processes)
    except queue.Empty as exc:
        raise RuntimeError("a multiprocessing worker produced no result") from exc
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        input_queue.close()
        input_queue.join_thread()
        output_queue.close()
        output_queue.join_thread()
    return ProcessRun(
        start_method=start_method,
        parent_pid=os.getpid(),
        started_worker_pids=tuple(_require_pid(process.pid) for process in processes),
        results=tuple(sorted(results, key=lambda result: result.task_id)),
    )


def count_deliveries_with_manager(
    task_ids: Iterable[str],
    *,
    workers: int,
    start_method: str = "spawn",
) -> dict[str, int]:
    """Update shared delivery counts through a manager server and proxies."""

    if workers < 1:
        raise ValueError("workers must be positive")
    tasks = tuple(task_ids)
    context = mp.get_context(start_method)
    with context.Manager() as manager:
        counts = manager.dict()
        lock = manager.Lock()
        chunks = tuple(tasks[index::workers] for index in range(min(workers, len(tasks))))
        processes = [
            context.Process(  # pyright: ignore[reportAttributeAccessIssue]
                target=_manager_worker,
                args=(chunk, counts, lock),
            )
            for chunk in chunks
        ]
        for process in processes:
            process.start()
        _join_or_raise(processes)
        return dict(counts)


def sum_shared_bytes(
    payload: bytes,
    *,
    workers: int,
    start_method: str = "spawn",
) -> SharedMemoryRun:
    """Sum disjoint slices without copying the complete payload to each child."""

    if workers < 1:
        raise ValueError("workers must be positive")
    if not payload:
        raise ValueError("payload must not be empty")
    context = mp.get_context(start_method)
    output_queue: Queue[tuple[int, int]] = context.Queue()
    segment = SharedMemory(create=True, size=len(payload))
    buffer = segment.buf
    if buffer is None:
        segment.close()
        segment.unlink()
        raise RuntimeError("shared-memory segment has no writable buffer")
    buffer[:] = payload
    del buffer
    worker_count = min(workers, len(payload))
    processes = []
    for index in range(worker_count):
        start = index * len(payload) // worker_count
        end = (index + 1) * len(payload) // worker_count
        processes.append(
            context.Process(  # pyright: ignore[reportAttributeAccessIssue]
                target=_shared_memory_worker,
                args=(segment.name, start, end, output_queue),
            )
        )
    try:
        for process in processes:
            process.start()
        partials = tuple(output_queue.get(timeout=10.0) for _ in processes)
        _join_or_raise(processes)
    except queue.Empty as exc:
        raise RuntimeError("a shared-memory worker produced no result") from exc
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        output_queue.close()
        output_queue.join_thread()
        segment.close()
        segment.unlink()
    return SharedMemoryRun(
        byte_sum=sum(partial[0] for partial in partials),
        worker_pids=tuple(partial[1] for partial in partials),
        segment_size=len(payload),
    )


def _queue_worker(
    input_queue: Queue[CpuWork | None],
    output_queue: Queue[CpuResult],
) -> None:
    while True:
        item = input_queue.get()
        if item is None:
            return
        output_queue.put(
            CpuResult(
                item.task_id,
                cpu_transform(item.seed, item.rounds),
                os.getpid(),
            )
        )


def _manager_worker(
    task_ids: tuple[str, ...],
    counts: MutableMapping[str, int],
    lock: LockProxy,
) -> None:
    for task_id in task_ids:
        lock.acquire()
        try:
            counts[task_id] = counts.get(task_id, 0) + 1
        finally:
            lock.release()


def _shared_memory_worker(
    name: str,
    start: int,
    end: int,
    output_queue: Queue[tuple[int, int]],
) -> None:
    segment = SharedMemory(name=name)
    try:
        buffer = segment.buf
        if buffer is None:
            raise RuntimeError("shared-memory segment has no readable buffer")
        subtotal = sum(buffer[start:end])
        del buffer
        output_queue.put((subtotal, os.getpid()))
    finally:
        segment.close()


def _join_or_raise(processes: list[mp.Process]) -> None:
    for process in processes:
        process.join(10.0)
    failed = tuple(process for process in processes if process.exitcode != 0)
    if failed:
        details = ", ".join(f"{process.name}={process.exitcode}" for process in failed)
        raise RuntimeError(f"multiprocessing workers failed: {details}")


def _require_pid(pid: int | None) -> int:
    if pid is None:
        raise RuntimeError("worker did not start")
    return pid
