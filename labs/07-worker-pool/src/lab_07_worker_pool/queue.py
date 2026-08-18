"""Typed queue abstractions with visibility timeouts."""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

T = TypeVar("T")
Clock = Callable[[], float]


@dataclass(frozen=True)
class Lease(Generic[T]):
    """A time-limited claim on a queue item."""

    lease_id: str
    item: T
    delivery_count: int


class VisibilityQueue(Protocol[T]):
    """Queue interface used by the worker pool."""

    visibility_timeout: float

    def put(self, item: T) -> None:
        """Enqueue an item."""
        ...

    def reserve(self, *, timeout: float | None = None) -> Lease[T] | None:
        """Lease the next visible item."""
        ...

    def acknowledge(self, lease_id: str) -> None:
        """Delete an item after successful handling."""
        ...

    def pending_count(self) -> int:
        """Count acknowledged and invisible items that still exist."""
        ...

    def visible_count(self) -> int:
        """Count items available to lease right now."""
        ...


class ManualClock:
    """Deterministic clock for visibility-timeout tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        with self._lock:
            self._now += seconds
            return self._now


@dataclass
class _QueueEntry(Generic[T]):
    message_id: str
    item: T
    delivery_count: int
    visible_at: float
    current_lease_id: str | None = None
    acknowledged: bool = False


class InMemoryVisibilityQueue(Generic[T]):
    """A minimal queue that models visibility timeouts and redelivery."""

    def __init__(
        self,
        *,
        visibility_timeout: float,
        clock: Clock | None = None,
    ) -> None:
        if visibility_timeout <= 0:
            raise ValueError("visibility_timeout must be positive")
        self.visibility_timeout = visibility_timeout
        self._clock = time.monotonic if clock is None else clock
        self._entries: list[_QueueEntry[T]] = []
        self._message_ids = itertools.count(1)
        self._lease_ids = itertools.count(1)
        self._condition = threading.Condition()

    def put(self, item: T) -> None:
        entry = _QueueEntry(
            message_id=f"message-{next(self._message_ids)}",
            item=item,
            delivery_count=0,
            visible_at=self._clock(),
        )
        with self._condition:
            self._entries.append(entry)
            self._condition.notify_all()

    def reserve(self, *, timeout: float | None = None) -> Lease[T] | None:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                entry = self._find_visible_unlocked()
                if entry is not None:
                    entry.delivery_count += 1
                    entry.visible_at = self._clock() + self.visibility_timeout
                    entry.current_lease_id = f"lease-{next(self._lease_ids)}"
                    return Lease(entry.current_lease_id, entry.item, entry.delivery_count)
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                else:
                    remaining = None
                self._condition.wait(self._next_wait_unlocked(remaining))

    def acknowledge(self, lease_id: str) -> None:
        with self._condition:
            for entry in self._entries:
                if not entry.acknowledged and entry.current_lease_id == lease_id:
                    entry.acknowledged = True
                    self._condition.notify_all()
                    return
        raise KeyError(f"unknown lease: {lease_id}")

    def pending_count(self) -> int:
        with self._condition:
            return sum(1 for entry in self._entries if not entry.acknowledged)

    def visible_count(self) -> int:
        with self._condition:
            now = self._clock()
            return sum(
                1 for entry in self._entries if not entry.acknowledged and entry.visible_at <= now
            )

    def _find_visible_unlocked(self) -> _QueueEntry[T] | None:
        now = self._clock()
        for entry in self._entries:
            if not entry.acknowledged and entry.visible_at <= now:
                return entry
        return None

    def _next_wait_unlocked(self, remaining: float | None) -> float | None:
        visible_delays = [
            max(0.0, entry.visible_at - self._clock())
            for entry in self._entries
            if not entry.acknowledged
        ]
        if not visible_delays:
            return remaining
        next_delay = min(visible_delays)
        if remaining is None:
            return next_delay
        return min(remaining, next_delay)
