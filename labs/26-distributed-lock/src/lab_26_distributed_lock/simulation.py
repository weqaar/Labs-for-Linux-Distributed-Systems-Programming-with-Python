"""Deterministic lock and lease simulation for the relay scheduler."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


class StaleFenceError(RuntimeError):
    """Raised when a resumed stale writer tries to write after losing a lease."""


class NoScriptError(RuntimeError):
    """Raised when a cached Lua script is absent from the simulated server."""


RELEASE_SCRIPT = """\
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


@dataclass
class SimulationClock:
    """In-memory clock used instead of wall-clock sleeps."""

    now_ms: int = 0

    def advance(self, delta_ms: int) -> int:
        if delta_ms < 0:
            raise ValueError("delta_ms must not be negative")
        self.now_ms += delta_ms
        return self.now_ms


@dataclass(frozen=True)
class RedisLockHandle:
    """Ownership token returned by a Redis-style lock acquisition."""

    key: str
    owner_token: str
    expires_at_ms: int


@dataclass(frozen=True)
class FencedLease:
    """Lease grant with a monotonic fence token."""

    key: str
    owner_id: str
    fence: int
    expires_at_ms: int


@dataclass(frozen=True)
class RelayWrite:
    """Write attempted by a lock holder."""

    resource_id: str
    writer_id: str
    payload: str


@dataclass(frozen=True)
class FencedRelayWrite:
    """Write attempted by a fenced lease holder."""

    resource_id: str
    writer_id: str
    payload: str
    fence: int


@dataclass(frozen=True)
class StoredValue:
    """Last value accepted by a simulated resource."""

    writer_id: str
    payload: str


@dataclass(frozen=True)
class FencedStoredValue:
    """Last fenced value accepted by a simulated resource."""

    writer_id: str
    payload: str
    fence: int


@dataclass(frozen=True)
class ScheduledInterval:
    """One interval decision retained by the in-memory scheduler ledger."""

    interval_start_ms: int
    scheduler_id: str
    fence: int


@dataclass(frozen=True)
class SchedulerAttempt:
    """Outcome of one scheduler instance trying to run an interval."""

    scheduler_id: str
    interval_start_ms: int
    acquired_lease: bool
    scheduled: bool
    fence: int | None = None


@dataclass(frozen=True)
class RedisCommandEvent:
    """One complete turn through the simulated Redis command executor."""

    sequence: int
    client_id: str
    command: str


class InMemoryRedisStore:
    """Serial Redis command executor with TTL and a volatile Lua script cache."""

    def __init__(self, clock: SimulationClock) -> None:
        self._clock = clock
        self._records: dict[str, RedisLockHandle] = {}
        self._counters: dict[str, int] = {}
        self._scripts: dict[str, str] = {}
        self._events: list[RedisCommandEvent] = []

    @property
    def events(self) -> tuple[RedisCommandEvent, ...]:
        return tuple(self._events)

    def set_nx_px(
        self,
        key: str,
        owner_token: str,
        ttl_ms: int,
        *,
        client_id: str = "client",
    ) -> bool:
        self._record(client_id, "SET NX PX")
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        self._purge_if_expired(key)
        if key in self._records:
            return False
        self._records[key] = RedisLockHandle(
            key=key,
            owner_token=owner_token,
            expires_at_ms=self._clock.now_ms + ttl_ms,
        )
        return True

    def get(self, key: str, *, client_id: str = "client") -> str | None:
        self._record(client_id, "GET")
        self._purge_if_expired(key)
        record = self._records.get(key)
        if record is None:
            return None
        return record.owner_token

    def delete(self, key: str, *, client_id: str = "client") -> bool:
        self._record(client_id, "DEL")
        self._purge_if_expired(key)
        return self._records.pop(key, None) is not None

    def counter_get(self, key: str, *, client_id: str) -> int:
        self._record(client_id, "GET")
        return self._counters.get(key, 0)

    def counter_set(self, key: str, value: int, *, client_id: str) -> None:
        self._record(client_id, "SET")
        self._counters[key] = value

    def counter_incr(self, key: str, *, client_id: str) -> int:
        self._record(client_id, "INCR")
        value = self._counters.get(key, 0) + 1
        self._counters[key] = value
        return value

    def script_load(self, source: str, *, client_id: str = "client") -> str:
        self._record(client_id, "SCRIPT LOAD")
        digest = hashlib.sha1(
            source.encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        self._scripts[digest] = source
        return digest

    def script_flush(self, *, client_id: str = "operator") -> None:
        self._record(client_id, "SCRIPT FLUSH")
        self._scripts.clear()

    def evalsha_compare_and_delete(
        self,
        digest: str,
        key: str,
        owner_token: str,
        *,
        client_id: str = "client",
    ) -> bool:
        source = self._scripts.get(digest)
        if source is None:
            self._record(client_id, "EVALSHA NOSCRIPT")
            raise NoScriptError(f"script {digest} is not loaded")
        if source != RELEASE_SCRIPT:
            raise ValueError("the teaching model only executes the release script")
        self._record(client_id, "EVALSHA GET-COMPARE-DEL")
        self._purge_if_expired(key)
        record = self._records.get(key)
        if record is None or record.owner_token != owner_token:
            return False
        del self._records[key]
        return True

    def _record(self, client_id: str, command: str) -> None:
        self._events.append(
            RedisCommandEvent(
                sequence=len(self._events) + 1,
                client_id=client_id,
                command=command,
            )
        )

    def _purge_if_expired(self, key: str) -> None:
        record = self._records.get(key)
        if record is not None and record.expires_at_ms <= self._clock.now_ms:
            del self._records[key]


class RedisStyleLockService:
    """Redis-style SET NX PX lock plus safe and unsafe release paths."""

    def __init__(self, store: InMemoryRedisStore, clock: SimulationClock) -> None:
        self._store = store
        self._clock = clock
        self._release_script_sha = self._store.script_load(
            RELEASE_SCRIPT,
            client_id="lock-service",
        )

    def acquire(self, key: str, owner_token: str, ttl_ms: int) -> RedisLockHandle | None:
        if not self._store.set_nx_px(
            key,
            owner_token,
            ttl_ms,
            client_id=owner_token,
        ):
            return None
        return RedisLockHandle(
            key=key,
            owner_token=owner_token,
            expires_at_ms=self._clock.now_ms + ttl_ms,
        )

    def release_naive(self, handle: RedisLockHandle) -> bool:
        return self._store.delete(handle.key, client_id=handle.owner_token)

    def release_compare_and_delete(self, handle: RedisLockHandle) -> bool:
        try:
            return self._store.evalsha_compare_and_delete(
                self._release_script_sha,
                handle.key,
                handle.owner_token,
                client_id=handle.owner_token,
            )
        except NoScriptError:
            self._release_script_sha = self._store.script_load(
                RELEASE_SCRIPT,
                client_id=handle.owner_token,
            )
            return self._store.evalsha_compare_and_delete(
                self._release_script_sha,
                handle.key,
                handle.owner_token,
                client_id=handle.owner_token,
            )


class UnfencedRelayStore:
    """Store that trusts a lock holder even after its lock expires."""

    def __init__(self) -> None:
        self._values: dict[str, StoredValue] = {}

    def write(self, write: RelayWrite) -> None:
        self._values[write.resource_id] = StoredValue(
            writer_id=write.writer_id,
            payload=write.payload,
        )

    def read(self, resource_id: str) -> StoredValue | None:
        return self._values.get(resource_id)


class FencedRelayStore:
    """Store that rejects stale writers using fence tokens."""

    def __init__(self) -> None:
        self._values: dict[str, FencedStoredValue] = {}
        self._highest_fence_by_resource: dict[str, int] = {}

    def write(self, write: FencedRelayWrite) -> None:
        highest_seen = self._highest_fence_by_resource.get(write.resource_id, 0)
        if write.fence < highest_seen:
            raise StaleFenceError(
                f"fence {write.fence} is stale for {write.resource_id}; {highest_seen} already seen"
            )
        self._highest_fence_by_resource[write.resource_id] = write.fence
        self._values[write.resource_id] = FencedStoredValue(
            writer_id=write.writer_id,
            payload=write.payload,
            fence=write.fence,
        )

    def read(self, resource_id: str) -> FencedStoredValue | None:
        return self._values.get(resource_id)


class FencedLeaseManager:
    """In-memory lease manager that issues monotonic fence tokens."""

    def __init__(self, clock: SimulationClock) -> None:
        self._clock = clock
        self._leases: dict[str, FencedLease] = {}
        self._fence_counters: dict[str, int] = {}

    def acquire(self, key: str, owner_id: str, ttl_ms: int) -> FencedLease | None:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        self._purge_if_expired(key)
        if key in self._leases:
            return None
        next_fence = self._fence_counters.get(key, 0) + 1
        self._fence_counters[key] = next_fence
        lease = FencedLease(
            key=key,
            owner_id=owner_id,
            fence=next_fence,
            expires_at_ms=self._clock.now_ms + ttl_ms,
        )
        self._leases[key] = lease
        return lease

    def release(self, lease: FencedLease) -> bool:
        self._purge_if_expired(lease.key)
        active = self._leases.get(lease.key)
        if active is None or active != lease:
            return False
        del self._leases[lease.key]
        return True

    def _purge_if_expired(self, key: str) -> None:
        lease = self._leases.get(key)
        if lease is not None and lease.expires_at_ms <= self._clock.now_ms:
            del self._leases[key]


class FencedScheduleLedger:
    """Deduplicate interval decisions in memory and reject stale fence tokens."""

    def __init__(self) -> None:
        self._highest_fence = 0
        self._intervals: dict[int, ScheduledInterval] = {}

    def record(self, interval_start_ms: int, scheduler_id: str, fence: int) -> bool:
        if fence < self._highest_fence:
            raise StaleFenceError(
                f"fence {fence} is stale for scheduler interval {interval_start_ms}"
            )
        self._highest_fence = max(self._highest_fence, fence)
        if interval_start_ms in self._intervals:
            return False
        self._intervals[interval_start_ms] = ScheduledInterval(
            interval_start_ms=interval_start_ms,
            scheduler_id=scheduler_id,
            fence=fence,
        )
        return True

    def decision(self, interval_start_ms: int) -> ScheduledInterval | None:
        return self._intervals.get(interval_start_ms)


class RelayScheduler:
    """Single relay scheduler instance."""

    def __init__(
        self,
        scheduler_id: str,
        lease_manager: FencedLeaseManager,
        ledger: FencedScheduleLedger,
        lease_ttl_ms: int,
    ) -> None:
        self._scheduler_id = scheduler_id
        self._lease_manager = lease_manager
        self._ledger = ledger
        self._lease_ttl_ms = lease_ttl_ms

    def try_schedule(self, interval_start_ms: int) -> SchedulerAttempt:
        lease = self._lease_manager.acquire(
            key="relay-scheduler",
            owner_id=self._scheduler_id,
            ttl_ms=self._lease_ttl_ms,
        )
        if lease is None:
            return SchedulerAttempt(
                scheduler_id=self._scheduler_id,
                interval_start_ms=interval_start_ms,
                acquired_lease=False,
                scheduled=False,
            )
        scheduled = self._ledger.record(
            interval_start_ms=interval_start_ms,
            scheduler_id=self._scheduler_id,
            fence=lease.fence,
        )
        self._lease_manager.release(lease)
        return SchedulerAttempt(
            scheduler_id=self._scheduler_id,
            interval_start_ms=interval_start_ms,
            acquired_lease=True,
            scheduled=scheduled,
            fence=lease.fence,
        )


class SchedulerSimulation:
    """Deterministic scheduler election over several instances."""

    def __init__(
        self,
        scheduler_ids: tuple[str, ...],
        clock: SimulationClock | None = None,
        interval_ms: int = 1_000,
        lease_ttl_ms: int = 250,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self.clock = clock or SimulationClock()
        self.interval_ms = interval_ms
        self._lease_manager = FencedLeaseManager(self.clock)
        self._ledger = FencedScheduleLedger()
        self._lease_ttl_ms = lease_ttl_ms
        self._schedulers = {
            scheduler_id: RelayScheduler(
                scheduler_id=scheduler_id,
                lease_manager=self._lease_manager,
                ledger=self._ledger,
                lease_ttl_ms=self._lease_ttl_ms,
            )
            for scheduler_id in scheduler_ids
        }

    def current_interval_start(self) -> int:
        return (self.clock.now_ms // self.interval_ms) * self.interval_ms

    def restart_instance(self, scheduler_id: str) -> None:
        if scheduler_id not in self._schedulers:
            raise ValueError(f"unknown scheduler {scheduler_id}")
        self._schedulers[scheduler_id] = RelayScheduler(
            scheduler_id=scheduler_id,
            lease_manager=self._lease_manager,
            ledger=self._ledger,
            lease_ttl_ms=self._lease_ttl_ms,
        )

    def run_interval(self, order: tuple[str, ...]) -> tuple[SchedulerAttempt, ...]:
        interval_start = self.current_interval_start()
        return tuple(
            self._schedulers[scheduler_id].try_schedule(interval_start) for scheduler_id in order
        )

    def scheduled_interval(self, interval_start_ms: int) -> ScheduledInterval | None:
        return self._ledger.decision(interval_start_ms)


__all__ = [
    "FencedLease",
    "FencedLeaseManager",
    "FencedRelayStore",
    "FencedRelayWrite",
    "FencedScheduleLedger",
    "FencedStoredValue",
    "InMemoryRedisStore",
    "NoScriptError",
    "RELEASE_SCRIPT",
    "RedisCommandEvent",
    "RedisLockHandle",
    "RedisStyleLockService",
    "RelayScheduler",
    "RelayWrite",
    "ScheduledInterval",
    "SchedulerAttempt",
    "SchedulerSimulation",
    "SimulationClock",
    "StaleFenceError",
    "StoredValue",
    "UnfencedRelayStore",
]
