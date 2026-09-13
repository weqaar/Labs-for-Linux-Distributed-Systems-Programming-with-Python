# Lab 33 Load Shedding

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
## Python REPL debugging session

After the editable install, inspect admission and retry policy:

```pycon
>>> import inspect
>>> import lab_33_load_shedding as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Construct one policy with a fake clock, inspect its immutable settings, and
step through accepted, rejected, and retried outcomes.
