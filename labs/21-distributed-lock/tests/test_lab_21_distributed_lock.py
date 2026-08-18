"""Tests for the lab_21_distributed_lock package."""

from __future__ import annotations

import pytest

from lab_21_distributed_lock import (
    FencedLeaseManager,
    FencedRelayStore,
    FencedRelayWrite,
    InMemoryRedisStore,
    RedisStyleLockService,
    RelayWrite,
    SchedulerSimulation,
    SimulationClock,
    StaleFenceError,
    UnfencedRelayStore,
    __version__,
)


def test_naive_delete_can_remove_someone_elses_lock() -> None:
    clock = SimulationClock()
    store = InMemoryRedisStore(clock)
    service = RedisStyleLockService(store, clock)
    first = service.acquire("relay:lock", owner_token="token-a", ttl_ms=10)
    assert first is not None

    clock.advance(11)
    second = service.acquire("relay:lock", owner_token="token-b", ttl_ms=10)

    assert second is not None
    assert service.release_naive(first)
    assert store.get("relay:lock") is None


def test_compare_and_delete_preserves_the_new_lock_holder() -> None:
    clock = SimulationClock()
    store = InMemoryRedisStore(clock)
    service = RedisStyleLockService(store, clock)
    first = service.acquire("relay:lock", owner_token="token-a", ttl_ms=10)
    assert first is not None

    clock.advance(11)
    second = service.acquire("relay:lock", owner_token="token-b", ttl_ms=10)

    assert second is not None
    assert not service.release_compare_and_delete(first)
    assert store.get("relay:lock") == "token-b"


def test_fenced_store_rejects_a_resumed_stale_writer() -> None:
    clock = SimulationClock()
    lease_manager = FencedLeaseManager(clock)
    unfenced_store = UnfencedRelayStore()
    fenced_store = FencedRelayStore()

    first = lease_manager.acquire("relay:writer", owner_id="scheduler-a", ttl_ms=5)
    assert first is not None

    clock.advance(6)
    second = lease_manager.acquire("relay:writer", owner_id="scheduler-b", ttl_ms=5)

    assert second is not None
    unfenced_store.write(
        RelayWrite(resource_id="relay:task:1", writer_id="scheduler-b", payload="worker-b")
    )
    fenced_store.write(
        FencedRelayWrite(
            resource_id="relay:task:1",
            writer_id="scheduler-b",
            payload="worker-b",
            fence=second.fence,
        )
    )

    unfenced_store.write(
        RelayWrite(resource_id="relay:task:1", writer_id="scheduler-a", payload="worker-a")
    )
    with pytest.raises(StaleFenceError, match="stale"):
        fenced_store.write(
            FencedRelayWrite(
                resource_id="relay:task:1",
                writer_id="scheduler-a",
                payload="worker-a",
                fence=first.fence,
            )
        )

    unfenced_value = unfenced_store.read("relay:task:1")
    fenced_value = fenced_store.read("relay:task:1")
    assert unfenced_value is not None
    assert fenced_value is not None
    assert unfenced_value.payload == "worker-a"
    assert fenced_value.payload == "worker-b"


def test_scheduler_runs_exactly_once_per_interval_across_a_restart() -> None:
    simulation = SchedulerSimulation(
        scheduler_ids=("scheduler-a", "scheduler-b", "scheduler-c"),
        clock=SimulationClock(),
        interval_ms=100,
        lease_ttl_ms=25,
    )

    first_round = simulation.run_interval(("scheduler-a", "scheduler-b", "scheduler-c"))
    simulation.restart_instance("scheduler-a")
    repeat_round = simulation.run_interval(("scheduler-a", "scheduler-b", "scheduler-c"))
    simulation.clock.advance(100)
    second_round = simulation.run_interval(("scheduler-b", "scheduler-c", "scheduler-a"))

    assert sum(1 for attempt in first_round if attempt.scheduled) == 1
    assert simulation.scheduled_interval(0) is not None
    assert sum(1 for attempt in repeat_round if attempt.scheduled) == 0
    assert sum(1 for attempt in second_round if attempt.scheduled) == 1
    first_interval = simulation.scheduled_interval(0)
    second_interval = simulation.scheduled_interval(100)
    assert first_interval is not None
    assert second_interval is not None
    assert first_interval.scheduler_id == "scheduler-a"


def test_version_is_exposed() -> None:
    assert __version__
