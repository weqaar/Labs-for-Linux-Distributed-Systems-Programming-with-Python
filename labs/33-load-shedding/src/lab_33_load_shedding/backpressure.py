"""Deterministic backpressure and retry models for the relay service."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class RandomSource(Protocol):
    """The small part of random.Random used by the retry policy."""

    def random(self) -> float:
        """Return a value in the half-open interval [0.0, 1.0)."""

        ...


class RelayOutcome(Enum):
    """Processing outcomes for a queued relay message."""

    SUCCEEDED = "succeeded"
    TRANSIENT_FAILURE = "transient_failure"
    POISON = "poison"


class BreakerState(Enum):
    """The circuit breaker states used by the relay worker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class RelayMessage:
    """A unit of work flowing through the relay service."""

    message_id: str
    payload: str
    attempts: int = 0

    def next_attempt(self) -> RelayMessage:
        """Return a copy for the next delivery attempt."""

        return RelayMessage(self.message_id, self.payload, self.attempts + 1)


@dataclass(frozen=True)
class AdmissionDecision:
    """The result of trying to add work to the bounded queue."""

    accepted: bool
    reason: str
    queue_depth: int
    retry_after_ms: int | None = None


@dataclass(frozen=True)
class CompletedRequest:
    """A request that completed after admission."""

    message_id: str
    accepted_at_ms: int
    started_at_ms: int
    completed_at_ms: int

    @property
    def latency_ms(self) -> int:
        """Total observed latency for the accepted request."""

        return self.completed_at_ms - self.accepted_at_ms


@dataclass(frozen=True)
class RetryInstruction:
    """A retry planned by the consumer after a transient failure."""

    message: RelayMessage
    delay_ms: int
    reason: str


@dataclass(frozen=True)
class DeadLetterEntry:
    """A message that can no longer be retried safely."""

    message: RelayMessage
    reason: str


@dataclass
class DeadLetterQueue:
    """A deterministic dead-letter queue used by tests."""

    entries: list[DeadLetterEntry] = field(default_factory=list)

    def add(self, message: RelayMessage, reason: str) -> None:
        """Store a dead-letter decision."""

        self.entries.append(DeadLetterEntry(message=message, reason=reason))


@dataclass
class ServiceBusRetryTopology:
    """Prevent application retries from multiplying SDK retries."""

    application_max_attempts: int
    sdk_max_retries: int = 0

    def validate(self) -> None:
        """Reject retry settings that would multiply attempts."""

        if self.application_max_attempts < 1:
            raise ValueError("application_max_attempts must be at least one")
        if self.sdk_max_retries != 0:
            raise ValueError("SDK retries must be disabled when application retries own backoff")


@dataclass
class ExponentialBackoffPolicy:
    """Compute deterministic backoff values with bounded jitter."""

    base_delay_ms: int
    multiplier: float
    max_delay_ms: int
    jitter_ratio: float

    def delay_ms(self, attempt: int, rng: RandomSource, retry_after_ms: int | None = None) -> int:
        """Return the next retry delay."""

        if attempt < 1:
            raise ValueError("attempt must be at least one")
        if self.base_delay_ms < 1:
            raise ValueError("base_delay_ms must be positive")
        raw_delay = min(
            int(self.base_delay_ms * (self.multiplier ** (attempt - 1))), self.max_delay_ms
        )
        jitter_window = raw_delay * self.jitter_ratio
        offset = (rng.random() * 2.0 - 1.0) * jitter_window
        candidate = max(0, int(round(raw_delay + offset)))
        if retry_after_ms is None:
            return candidate
        return max(candidate, retry_after_ms)


@dataclass
class RetryBudget:
    """A token budget that caps extra retries."""

    ratio: float
    maximum_tokens: float
    initial_tokens: float = 0.0
    _tokens: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.ratio <= 0.0:
            raise ValueError("ratio must be positive")
        if self.maximum_tokens < 1.0:
            raise ValueError("maximum_tokens must be at least one")
        if self.initial_tokens < 0.0:
            raise ValueError("initial_tokens must not be negative")
        self._tokens = min(self.maximum_tokens, self.initial_tokens)

    @property
    def tokens(self) -> float:
        """Expose the current token count for tests."""

        return self._tokens

    def record_primary_request(self, count: int = 1) -> None:
        """Top up the retry budget from fresh traffic."""

        if count < 0:
            raise ValueError("count must not be negative")
        self._tokens = min(self.maximum_tokens, self._tokens + self.ratio * count)

    def allow_retry(self) -> bool:
        """Consume one retry token if available."""

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


@dataclass
class CircuitBreaker:
    """Track whether the relay worker should stop calling a failing dependency."""

    failure_threshold: int
    recovery_timeout_ms: int
    half_open_successes: int = 1
    state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _open_until_ms: int = field(default=0, init=False, repr=False)
    _half_open_success_count: int = field(default=0, init=False, repr=False)

    def allow(self, now_ms: int) -> bool:
        """Return True when the dependency call is allowed."""

        if self.state is BreakerState.OPEN:
            if now_ms < self._open_until_ms:
                return False
            self.state = BreakerState.HALF_OPEN
            self._half_open_success_count = 0
            return True
        return True

    def retry_after_ms(self, now_ms: int) -> int:
        """Return the remaining open time, rounded up to one millisecond."""

        if self.state is not BreakerState.OPEN:
            return 0
        return max(self._open_until_ms - now_ms, 1)

    def record_success(self) -> None:
        """Record a successful dependency call."""

        if self.state is BreakerState.HALF_OPEN:
            self._half_open_success_count += 1
            if self._half_open_success_count >= self.half_open_successes:
                self.state = BreakerState.CLOSED
                self._consecutive_failures = 0
                self._half_open_success_count = 0
            return
        self._consecutive_failures = 0

    def record_failure(self, now_ms: int) -> None:
        """Record a failed dependency call and maybe open the breaker."""

        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least one")
        if self.recovery_timeout_ms < 1:
            raise ValueError("recovery_timeout_ms must be positive")
        if self.state is BreakerState.HALF_OPEN:
            self._open(now_ms)
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._open(now_ms)

    def _open(self, now_ms: int) -> None:
        self.state = BreakerState.OPEN
        self._open_until_ms = now_ms + self.recovery_timeout_ms
        self._consecutive_failures = 0
        self._half_open_success_count = 0


@dataclass(frozen=True)
class _QueuedMessage:
    message: RelayMessage
    accepted_at_ms: int


@dataclass(frozen=True)
class _InflightMessage:
    message: RelayMessage
    accepted_at_ms: int
    started_at_ms: int
    completed_at_ms: int


@dataclass
class AdmissionController:
    """Bound worker concurrency and queue depth."""

    workers: int
    queue_limit: int
    service_time_ms: int
    completed: list[CompletedRequest] = field(default_factory=list)
    max_observed_queue_depth: int = field(default=0, init=False)
    _queue: deque[_QueuedMessage] = field(default_factory=deque, init=False, repr=False)
    _inflight: list[_InflightMessage] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.workers < 1:
            raise ValueError("workers must be at least one")
        if self.queue_limit < 0:
            raise ValueError("queue_limit must not be negative")
        if self.service_time_ms < 1:
            raise ValueError("service_time_ms must be positive")

    @property
    def queue_depth(self) -> int:
        """Return the current waiting depth."""

        return len(self._queue)

    def submit(self, message: RelayMessage, now_ms: int) -> AdmissionDecision:
        """Attempt to enqueue *message* at *now_ms*."""

        self.advance_to(now_ms)
        if len(self._inflight) < self.workers:
            self._start_message(message, accepted_at_ms=now_ms, started_at_ms=now_ms)
            return AdmissionDecision(True, "accepted", self.queue_depth)
        if len(self._queue) < self.queue_limit:
            self._queue.append(_QueuedMessage(message=message, accepted_at_ms=now_ms))
            self.max_observed_queue_depth = max(self.max_observed_queue_depth, self.queue_depth)
            return AdmissionDecision(True, "queued", self.queue_depth)
        retry_after_ms = self._next_completion_delay(now_ms)
        return AdmissionDecision(False, "queue_full", self.queue_depth, retry_after_ms)

    def advance_to(self, now_ms: int) -> None:
        """Complete any work that should finish by *now_ms*."""

        while self._inflight:
            next_item = min(self._inflight, key=lambda item: item.completed_at_ms)
            if next_item.completed_at_ms > now_ms:
                break
            self._inflight.remove(next_item)
            self.completed.append(
                CompletedRequest(
                    message_id=next_item.message.message_id,
                    accepted_at_ms=next_item.accepted_at_ms,
                    started_at_ms=next_item.started_at_ms,
                    completed_at_ms=next_item.completed_at_ms,
                )
            )
            if self._queue:
                queued = self._queue.popleft()
                self._start_message(
                    queued.message,
                    accepted_at_ms=queued.accepted_at_ms,
                    started_at_ms=next_item.completed_at_ms,
                )

    def drain(self) -> None:
        """Process all currently accepted work."""

        while self._inflight:
            next_completion = min(item.completed_at_ms for item in self._inflight)
            self.advance_to(next_completion)

    def _start_message(
        self, message: RelayMessage, accepted_at_ms: int, started_at_ms: int
    ) -> None:
        self._inflight.append(
            _InflightMessage(
                message=message,
                accepted_at_ms=accepted_at_ms,
                started_at_ms=started_at_ms,
                completed_at_ms=started_at_ms + self.service_time_ms,
            )
        )

    def _next_completion_delay(self, now_ms: int) -> int:
        next_completion = min(item.completed_at_ms for item in self._inflight)
        return max(next_completion - now_ms, 1)


@dataclass
class RetryingConsumer:
    """Apply retries, circuit breaking, and dead-letter handling."""

    backoff_policy: ExponentialBackoffPolicy
    retry_budget: RetryBudget
    retry_topology: ServiceBusRetryTopology
    circuit_breaker: CircuitBreaker
    dead_letters: DeadLetterQueue = field(default_factory=DeadLetterQueue)

    def process(
        self,
        message: RelayMessage,
        outcome: RelayOutcome,
        now_ms: int,
        rng: RandomSource,
        retry_after_ms: int | None = None,
    ) -> RetryInstruction | None:
        """Handle a delivery outcome and maybe schedule a retry."""

        self.retry_topology.validate()
        if not self.circuit_breaker.allow(now_ms):
            return RetryInstruction(
                message=message.next_attempt(),
                delay_ms=self.circuit_breaker.retry_after_ms(now_ms),
                reason="circuit_open",
            )
        if outcome is RelayOutcome.SUCCEEDED:
            self.circuit_breaker.record_success()
            return None
        if outcome is RelayOutcome.POISON:
            self.dead_letters.add(message.next_attempt(), "poison_message")
            return None

        self.circuit_breaker.record_failure(now_ms)
        next_message = message.next_attempt()
        if next_message.attempts >= self.retry_topology.application_max_attempts:
            self.dead_letters.add(next_message, "retry_limit_reached")
            return None
        if not self.retry_budget.allow_retry():
            self.dead_letters.add(next_message, "retry_budget_exhausted")
            return None
        delay_ms = self.backoff_policy.delay_ms(next_message.attempts, rng, retry_after_ms)
        return RetryInstruction(message=next_message, delay_ms=delay_ms, reason="transient_failure")
