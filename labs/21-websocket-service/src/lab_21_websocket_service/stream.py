"""In-memory WebSocket event streaming for the relay task service."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from lab_21_websocket_service.contract import BearerToken, TaskAction, TaskEvent, TaskState


@dataclass
class FakeClock:
    """Clock shared by the broker and the WebSocket session."""

    monotonic_ms: int = 0
    wall_time: datetime = datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc)

    def advance(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("milliseconds must not be negative")
        self.monotonic_ms += milliseconds
        self.wall_time += timedelta(milliseconds=milliseconds)


@dataclass(frozen=True)
class KeepalivePolicy:
    """How long an idle stream waits before it sends a ping."""

    idle_interval_ms: int = 30_000

    def __post_init__(self) -> None:
        if self.idle_interval_ms <= 0:
            raise ValueError("idle_interval_ms must be positive")


@dataclass(frozen=True)
class ReconnectPolicy:
    """Exponential backoff with deterministic jitter."""

    base_delay_ms: int = 250
    max_delay_ms: int = 5_000
    jitter_spread_ms: int = 250

    def __post_init__(self) -> None:
        if self.base_delay_ms <= 0 or self.max_delay_ms <= 0:
            raise ValueError("reconnect delays must be positive")
        if self.jitter_spread_ms < 0:
            raise ValueError("jitter_spread_ms must not be negative")

    def delay_ms(self, *, attempt: int, client_id: str) -> int:
        if attempt < 0:
            raise ValueError("attempt must not be negative")
        backoff = min(self.base_delay_ms * (2**attempt), self.max_delay_ms)
        return backoff + _deterministic_jitter(client_id, attempt, self.jitter_spread_ms)


class EventBroker(Protocol):
    """Broker abstraction used by the WebSocket service."""

    def events_after(self, sequence: int) -> tuple[TaskEvent, ...]: ...
    def subscribe(self, callback: Callable[[TaskEvent], None]) -> Callable[[], None]: ...


class OutboundQueueFullError(RuntimeError):
    """Raised when a slow client exhausts the outbound queue."""


class FakeConnection:
    """In-memory WebSocket connection with a bounded outbound queue."""

    def __init__(self, *, outbound_limit: int = 8) -> None:
        if outbound_limit < 1:
            raise ValueError("outbound_limit must be at least one")
        self._outbound_limit = outbound_limit
        self._frames: deque[dict[str, object]] = deque()
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    def send(self, frame: dict[str, object]) -> None:
        if self.closed:
            raise RuntimeError("connection is closed")
        if len(self._frames) >= self._outbound_limit:
            raise OutboundQueueFullError("outbound queue is full")
        self._frames.append(frame)

    def close(self, code: int, reason: str) -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    def drain(self) -> list[dict[str, object]]:
        drained = list(self._frames)
        self._frames.clear()
        return drained


class InMemoryBroker:
    """History and fan-out broker used by the fake WebSocket service."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._events: list[TaskEvent] = []
        self._subscribers: dict[int, Callable[[TaskEvent], None]] = {}
        self._next_sequence = 0
        self._next_subscription_id = 0

    def publish(
        self, *, task_id: str, action: TaskAction, state: TaskState, detail: str
    ) -> TaskEvent:
        self._next_sequence += 1
        event = TaskEvent(
            sequence=self._next_sequence,
            id=task_id,
            action=action,
            state=state,
            detail=detail,
            timestamp=self._clock.wall_time,
        )
        self._events.append(event)
        for callback in tuple(self._subscribers.values()):
            callback(event)
        return event

    def events_after(self, sequence: int) -> tuple[TaskEvent, ...]:
        return tuple(event for event in self._events if event.sequence > sequence)

    def subscribe(self, callback: Callable[[TaskEvent], None]) -> Callable[[], None]:
        self._next_subscription_id += 1
        subscription_id = self._next_subscription_id
        self._subscribers[subscription_id] = callback

        def unsubscribe() -> None:
            self._subscribers.pop(subscription_id, None)

        return unsubscribe


class RelayStreamSession:
    """One WebSocket session with replay, keepalive and expiry checks."""

    def __init__(
        self,
        broker: EventBroker,
        connection: FakeConnection,
        clock: FakeClock,
        token: BearerToken,
        *,
        keepalive: KeepalivePolicy = KeepalivePolicy(),
        resume_after: int = 0,
    ) -> None:
        if resume_after < 0:
            raise ValueError("resume_after must not be negative")
        self._broker = broker
        self._connection = connection
        self._clock = clock
        self._token = token
        self._keepalive = keepalive
        self._last_send_ms = clock.monotonic_ms
        self._last_sequence = resume_after
        self._unsubscribe: Callable[[], None] | None = None

        if self._token.expires_at_ms <= self._clock.monotonic_ms:
            self._connection.close(4001, "bearer token expired")
            return

        for event in self._broker.events_after(resume_after):
            self._deliver(event)
        if not self._connection.closed:
            self._unsubscribe = self._broker.subscribe(self._deliver)

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    def tick(self) -> None:
        if self._connection.closed:
            return
        if self._token.expires_at_ms <= self._clock.monotonic_ms:
            self.close(4001, "bearer token expired")
            return
        idle_for = self._clock.monotonic_ms - self._last_send_ms
        if idle_for >= self._keepalive.idle_interval_ms:
            self._send_frame({"type": "ping", "resume_from": self._last_sequence})

    def close_for_restart(self) -> None:
        self.close(1001, "service restarting")

    def close(self, code: int, reason: str) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if not self._connection.closed:
            self._connection.close(code, reason)

    def _deliver(self, event: TaskEvent) -> None:
        self._last_sequence = event.sequence
        self._send_frame(event.to_frame())

    def _send_frame(self, frame: dict[str, object]) -> None:
        try:
            self._connection.send(frame)
        except OutboundQueueFullError:
            self.close(4002, "slow client")
            return
        self._last_send_ms = self._clock.monotonic_ms


def _deterministic_jitter(client_id: str, attempt: int, spread_ms: int) -> int:
    if spread_ms == 0:
        return 0
    seed = sum(ord(character) for character in client_id) + (attempt * 31)
    return seed % (spread_ms + 1)
