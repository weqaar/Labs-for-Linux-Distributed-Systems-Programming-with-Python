# Lab 33 Load Shedding

This lab rejects new SigRaft jobs when its workers and queue are full.
The Python import name is `relay`; the program does not relay traffic.
The supplied simulation demonstrates bounds for a fixed time per job
without sleeping or waiting on Azure Service Bus. It does not measure or
guarantee latency in a deployed service.

## Goal and activities

Reject work before capacity is exhausted and keep retries from multiplying
load. You will advance simulated time and inspect decisions, not run a broker or
load-test a web server. Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Runtime logic uses standard-library values and injected randomness;
dependencies are declared in `pyproject.toml`.

1. Install and fill the admission model in the REPL.
2. Run `pytest -q tests/test_lab_33_load_shedding.py`. For two workers and
   four queue slots, twelve simultaneous requests yield six admissions and
   six rejections in the supplied test.
3. Inspect `Retry-After`, bounded jitter and the shared retry budget. Compare
   the modeled application retry limit with disabled SDK retries.
4. Step the breaker through open and half-open, then inspect poison-message
   dead-lettering. Add a boundary case without sleeping.

## Components

Follow the decision in order: admission accepts or rejects new work, retry
policy decides when another attempt is allowed, and the consumer moves
messages it can no longer process to a dead-letter queue. A circuit breaker stops calls temporarily
after failures; a half-open breaker permits a limited recovery probe.

- `AdmissionController` caps worker slots and queue depth, then returns an
  explicit `Retry-After` hint when the service is full.
- `ExponentialBackoffPolicy` adds bounded jitter and honours the service hint.
- `RetryBudget` and `ServiceBusRetryTopology` keep retries in one place instead
  of multiplying application retries with SDK retries.
- `CircuitBreaker` models closed, open, and half-open states.
- `RetryingConsumer` sends poison or exhausted messages to the dead-letter queue.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

## Tests

`tests/test_lab_33_load_shedding.py` holds the unit tests. They check the
admission bounds, backoff with jitter, the retry budget, the breaker states
and dead-letter decisions one component at a time.

`tests/test_functional.py` holds the functional tests. They drive
`AdmissionController.submit` and `RetryingConsumer.process` across a stream
of jobs: a full service rejects `task-19` and admits it after the
`Retry-After` hint, a failing dependency opens the breaker until recovery,
and poison or repeatedly failing jobs end in the dead-letter queue.

Run each kind alone with `pytest tests/test_lab_33_load_shedding.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

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
>>> from lab_33_load_shedding.backpressure import AdmissionController, RelayMessage
>>> controller = AdmissionController(workers=1, queue_limit=1, service_time_ms=50)
>>> decisions = [controller.submit(RelayMessage(f"task-{n}", "index"), 0)
...              for n in (17, 18, 19)]
>>> [decision.accepted for decision in decisions]
[True, True, False]
>>> controller.drain()
```

At time zero, one job takes the worker and another takes the only queue slot.
The third is rejected. `drain()` advances the simulation to complete accepted
work; it does not wait for a real worker. Inspect the rejected decision's
retry hint before adding a later submission.

## Contribution and completion

These policies inform admission and retry design for the SigRaft
job-orchestration web service; Lab 39 does not import this simulator.
Finish when you can distinguish capacity rejection, retry exhaustion and a
poison message, and `pybootstrap check` exits 0. Exit 1 means findings;
exit 2 means a gate could not run. No Azure subscription is needed.
Exit Python to discard queues, counters and the modeled dead-letter list.
