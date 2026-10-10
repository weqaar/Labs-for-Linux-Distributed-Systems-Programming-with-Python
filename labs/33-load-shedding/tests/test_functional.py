"""Functional tests for the load shedding checkpoint.

These tests drive the two public front doors of the checkpoint the way the
job service would: ``AdmissionController.submit`` for new jobs and
``RetryingConsumer.process`` for delivery outcomes. Each test follows a
stream of jobs from submission or delivery to its final decision, using
simulated milliseconds and a scripted random source instead of sleeps.
"""

from __future__ import annotations

import pytest

from lab_33_load_shedding import (
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


class ScriptedRandom:
    """Random source that always returns the midpoint, so jitter is zero."""

    def random(self) -> float:
        return 0.5


def _consumer(*, max_attempts: int, failure_threshold: int) -> RetryingConsumer:
    return RetryingConsumer(
        backoff_policy=ExponentialBackoffPolicy(
            base_delay_ms=100, multiplier=2.0, max_delay_ms=1_000, jitter_ratio=0.1
        ),
        retry_budget=RetryBudget(ratio=1.0, maximum_tokens=10.0, initial_tokens=10.0),
        retry_topology=ServiceBusRetryTopology(application_max_attempts=max_attempts),
        circuit_breaker=CircuitBreaker(
            failure_threshold=failure_threshold, recovery_timeout_ms=100
        ),
    )


def test_full_service_rejects_a_job_and_admits_it_after_retry_after() -> None:
    controller = AdmissionController(workers=1, queue_limit=1, service_time_ms=50)

    first = controller.submit(RelayMessage("task-17", "index"), 0)
    second = controller.submit(RelayMessage("task-18", "index"), 0)
    rejected = controller.submit(RelayMessage("task-19", "index"), 0)

    assert (first.accepted, first.reason) == (True, "accepted")
    assert (second.accepted, second.reason) == (True, "queued")
    assert (rejected.accepted, rejected.reason) == (False, "queue_full")
    assert rejected.retry_after_ms == 50

    retried = controller.submit(RelayMessage("task-19", "index"), rejected.retry_after_ms)
    assert (retried.accepted, retried.reason) == (True, "queued")
    controller.drain()

    assert [(item.message_id, item.completed_at_ms) for item in controller.completed] == [
        ("task-17", 50),
        ("task-18", 100),
        ("task-19", 150),
    ]
    assert controller.queue_depth == 0


def test_failing_dependency_opens_the_breaker_until_recovery_timeout() -> None:
    consumer = _consumer(max_attempts=5, failure_threshold=2)
    rng = ScriptedRandom()
    message = RelayMessage("task-17", "index")

    first = consumer.process(message, RelayOutcome.TRANSIENT_FAILURE, now_ms=0, rng=rng)
    assert first is not None
    second = consumer.process(first.message, RelayOutcome.TRANSIENT_FAILURE, now_ms=10, rng=rng)
    assert second is not None
    assert [first.delay_ms, second.delay_ms] == [100, 200]
    assert consumer.circuit_breaker.state is BreakerState.OPEN

    held = consumer.process(second.message, RelayOutcome.SUCCEEDED, now_ms=20, rng=rng)
    assert held is not None
    assert (held.reason, held.delay_ms, held.message.attempts) == ("circuit_open", 90, 3)

    recovered = consumer.process(held.message, RelayOutcome.SUCCEEDED, now_ms=110, rng=rng)
    assert recovered is None
    assert consumer.circuit_breaker.state is BreakerState.CLOSED
    assert consumer.dead_letters.entries == []


def test_poison_and_repeatedly_failing_jobs_end_in_the_dead_letter_queue() -> None:
    consumer = _consumer(max_attempts=3, failure_threshold=10)
    rng = ScriptedRandom()

    assert consumer.process(RelayMessage("task-17", "x"), RelayOutcome.POISON, 0, rng) is None

    delivery = RelayMessage("task-18", "index")
    now_ms = 0
    for _ in range(10):
        retry = consumer.process(delivery, RelayOutcome.TRANSIENT_FAILURE, now_ms, rng)
        if retry is None:
            break
        delivery = retry.message
        now_ms += retry.delay_ms
    else:
        pytest.fail("task-18 was still being retried after ten deliveries")

    assert [
        (entry.message.message_id, entry.message.attempts, entry.reason)
        for entry in consumer.dead_letters.entries
    ] == [
        ("task-17", 1, "poison_message"),
        ("task-18", 3, "retry_limit_reached"),
    ]
