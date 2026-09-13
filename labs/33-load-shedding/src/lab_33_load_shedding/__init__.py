"""Relay load shedding checkpoint artifacts for Chapter 33."""

from __future__ import annotations

from lab_33_load_shedding.backpressure import (
    AdmissionController,
    AdmissionDecision,
    BreakerState,
    CircuitBreaker,
    CompletedRequest,
    DeadLetterEntry,
    DeadLetterQueue,
    ExponentialBackoffPolicy,
    RelayMessage,
    RelayOutcome,
    RetryBudget,
    RetryingConsumer,
    RetryInstruction,
    ServiceBusRetryTopology,
)

__version__ = "0.1.0"

__all__ = [
    "AdmissionController",
    "AdmissionDecision",
    "BreakerState",
    "CircuitBreaker",
    "CompletedRequest",
    "DeadLetterEntry",
    "DeadLetterQueue",
    "ExponentialBackoffPolicy",
    "RelayMessage",
    "RelayOutcome",
    "RetryBudget",
    "RetryInstruction",
    "RetryingConsumer",
    "ServiceBusRetryTopology",
    "__version__",
]
