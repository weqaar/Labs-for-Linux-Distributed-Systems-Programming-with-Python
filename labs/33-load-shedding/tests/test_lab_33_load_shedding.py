"""Tests for the relay load shedding checkpoint."""

from __future__ import annotations

import pytest

from lab_33_load_shedding import __version__
from lab_33_load_shedding.backpressure import (
    AdmissionController,
    BreakerState,
    CircuitBreaker,
    ExponentialBackoffPolicy,
    RelayMessage,
    RelayOutcome,
    RetryBudget,
    RetryingConsumer,
    ServiceBusRetryTopology,
)


class FakeRandom:
    def __init__(self, *values: float) -> None:
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0)


def test_overload_keeps_queue_depth_and_latency_bounded() -> None:
    controller = AdmissionController(workers=2, queue_limit=4, service_time_ms=50)

    decisions = [controller.submit(RelayMessage(f"m-{index}", "payload"), 0) for index in range(12)]
    controller.drain()

    accepted = [decision for decision in decisions if decision.accepted]
    rejected = [decision for decision in decisions if not decision.accepted]
    assert len(accepted) == 6
    assert len(rejected) == 6
    assert controller.max_observed_queue_depth == 4
    assert all(decision.retry_after_ms == 50 for decision in rejected)
    assert max(item.latency_ms for item in controller.completed) == 150


def test_backoff_uses_jitter_and_respects_retry_after_hint() -> None:
    policy = ExponentialBackoffPolicy(
        base_delay_ms=100,
        multiplier=2.0,
        max_delay_ms=1_000,
        jitter_ratio=0.2,
    )

    assert policy.delay_ms(attempt=2, rng=FakeRandom(0.75), retry_after_ms=250) == 250
    assert policy.delay_ms(attempt=3, rng=FakeRandom(0.0)) == 320


def test_retry_budget_limits_retries_and_topology_disables_sdk_retries() -> None:
    budget = RetryBudget(ratio=0.5, maximum_tokens=2.0)
    budget.record_primary_request(2)

    assert budget.allow_retry() is True
    assert budget.allow_retry() is False

    ServiceBusRetryTopology(application_max_attempts=3, sdk_max_retries=0).validate()
    with pytest.raises(ValueError, match="SDK retries"):
        ServiceBusRetryTopology(application_max_attempts=3, sdk_max_retries=2).validate()


def test_circuit_breaker_transitions_closed_open_half_open_closed() -> None:
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_ms=30)

    assert breaker.allow(0) is True
    breaker.record_failure(0)
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure(10)
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow(20) is False
    assert breaker.retry_after_ms(20) == 20
    assert breaker.allow(40) is True
    assert breaker.state is BreakerState.HALF_OPEN
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED


def test_poison_messages_go_to_dead_letter_queue() -> None:
    consumer = RetryingConsumer(
        backoff_policy=ExponentialBackoffPolicy(100, 2.0, 1_000, 0.1),
        retry_budget=RetryBudget(ratio=1.0, maximum_tokens=2.0, initial_tokens=1.0),
        retry_topology=ServiceBusRetryTopology(application_max_attempts=3),
        circuit_breaker=CircuitBreaker(failure_threshold=2, recovery_timeout_ms=50),
    )

    result = consumer.process(
        RelayMessage("poison-1", "payload"),
        RelayOutcome.POISON,
        now_ms=0,
        rng=FakeRandom(0.5),
    )

    assert result is None
    assert consumer.dead_letters.entries[0].reason == "poison_message"
    assert consumer.dead_letters.entries[0].message.attempts == 1


def test_transient_failures_retry_then_dead_letter_when_budget_is_gone() -> None:
    consumer = RetryingConsumer(
        backoff_policy=ExponentialBackoffPolicy(100, 2.0, 1_000, 0.0),
        retry_budget=RetryBudget(ratio=1.0, maximum_tokens=1.0, initial_tokens=1.0),
        retry_topology=ServiceBusRetryTopology(application_max_attempts=4),
        circuit_breaker=CircuitBreaker(failure_threshold=5, recovery_timeout_ms=50),
    )

    first_retry = consumer.process(
        RelayMessage("transient-1", "payload"),
        RelayOutcome.TRANSIENT_FAILURE,
        now_ms=0,
        rng=FakeRandom(0.5),
        retry_after_ms=150,
    )
    assert first_retry is not None
    second_retry = consumer.process(
        first_retry.message,
        RelayOutcome.TRANSIENT_FAILURE,
        now_ms=10,
        rng=FakeRandom(0.5),
    )

    assert first_retry.delay_ms == 150
    assert first_retry.message.attempts == 1
    assert second_retry is None
    assert consumer.dead_letters.entries[-1].reason == "retry_budget_exhausted"
    assert consumer.dead_letters.entries[-1].message.attempts == 2


def test_version_is_exposed() -> None:
    assert __version__
