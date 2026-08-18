# Lab 28 Load Shedding

This checkpoint replaces queue growth by making the relay service say no early.
All behaviour is deterministic, so the tests prove bounded latency without
sleeping or waiting on Azure Service Bus.

## Components

- `AdmissionController` caps worker slots and queue depth, then returns an
  explicit `Retry-After` hint when the service is full.
- `ExponentialBackoffPolicy` adds bounded jitter and honours the service hint.
- `RetryBudget` and `ServiceBusRetryTopology` keep retries in one place instead
  of multiplying application retries with SDK retries.
- `CircuitBreaker` models closed, open, and half-open states.
- `RetryingConsumer` sends poison or exhausted messages to the dead-letter queue.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
