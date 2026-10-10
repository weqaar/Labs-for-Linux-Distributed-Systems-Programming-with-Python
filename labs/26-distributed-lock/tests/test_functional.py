"""Functional tests for the distributed lock checkpoint.

These tests drive the composed simulation through the package's public API:
the Redis-style lock service over ``InMemoryRedisStore``, the fenced lease
manager writing to a ``FencedRelayStore``, and ``SchedulerSimulation``. Each
test follows one complete worker or scheduler workflow while the manual
``SimulationClock`` stands in for elapsed time, so no Redis server is needed.
"""

from __future__ import annotations

import pytest

from lab_26_distributed_lock import (
    FencedLeaseManager,
    FencedRelayStore,
    FencedRelayWrite,
    InMemoryRedisStore,
    RedisStyleLockService,
    SchedulerSimulation,
    SimulationClock,
    StaleFenceError,
)

JOB_RESOURCE = "relay:task-17"


def test_competing_workers_take_turns_on_the_job_lock() -> None:
    clock = SimulationClock()
    store = InMemoryRedisStore(clock)
    service = RedisStyleLockService(store, clock)

    first = service.acquire(JOB_RESOURCE, owner_token="worker-a", ttl_ms=100)
    blocked = service.acquire(JOB_RESOURCE, owner_token="worker-b", ttl_ms=100)
    assert first is not None
    assert blocked is None
    assert store.get(JOB_RESOURCE) == "worker-a"

    clock.advance(10)
    assert service.release_compare_and_delete(first)
    second = service.acquire(JOB_RESOURCE, owner_token="worker-b", ttl_ms=100)

    assert second is not None
    assert store.get(JOB_RESOURCE) == "worker-b"
    assert not service.release_compare_and_delete(first)
    assert store.get(JOB_RESOURCE) == "worker-b"


def test_job_progresses_to_succeeded_while_paused_worker_is_fenced_out() -> None:
    clock = SimulationClock()
    leases = FencedLeaseManager(clock)
    destination = FencedRelayStore()

    paused = leases.acquire(JOB_RESOURCE, owner_id="worker-a", ttl_ms=50)
    assert paused is not None
    destination.write(
        FencedRelayWrite(JOB_RESOURCE, "worker-a", payload="running", fence=paused.fence)
    )

    clock.advance(60)
    current = leases.acquire(JOB_RESOURCE, owner_id="worker-b", ttl_ms=50)
    assert current is not None
    assert current.fence > paused.fence
    destination.write(
        FencedRelayWrite(JOB_RESOURCE, "worker-b", payload="succeeded", fence=current.fence)
    )

    with pytest.raises(StaleFenceError, match="stale"):
        destination.write(
            FencedRelayWrite(JOB_RESOURCE, "worker-a", payload="failed", fence=paused.fence)
        )
    assert not leases.release(paused)
    assert leases.release(current)

    stored = destination.read(JOB_RESOURCE)
    assert stored is not None
    assert (stored.writer_id, stored.payload, stored.fence) == (
        "worker-b",
        "succeeded",
        current.fence,
    )


def test_scheduler_cluster_runs_each_interval_once_across_a_restart() -> None:
    simulation = SchedulerSimulation(
        scheduler_ids=("scheduler-a", "scheduler-b"),
        interval_ms=1_000,
        lease_ttl_ms=200,
    )

    decisions: list[str | None] = []
    for order in (
        ("scheduler-a", "scheduler-b"),
        ("scheduler-b", "scheduler-a"),
        ("scheduler-b", "scheduler-a"),
    ):
        attempts = simulation.run_interval(order)
        assert sum(1 for attempt in attempts if attempt.scheduled) == 1
        repeat = simulation.run_interval(order)
        assert not any(attempt.scheduled for attempt in repeat)
        decision = simulation.scheduled_interval(simulation.current_interval_start())
        decisions.append(None if decision is None else decision.scheduler_id)
        simulation.restart_instance(order[0])
        simulation.clock.advance(1_000)

    assert decisions == ["scheduler-a", "scheduler-b", "scheduler-b"]


def test_restarting_an_unknown_scheduler_is_rejected() -> None:
    simulation = SchedulerSimulation(scheduler_ids=("scheduler-a",))

    with pytest.raises(ValueError, match="unknown scheduler scheduler-z"):
        simulation.restart_instance("scheduler-z")
