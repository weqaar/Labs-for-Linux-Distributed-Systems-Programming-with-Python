# Lab 26 Distributed Lock

This lab examines what happens when a worker pauses long enough to
lose ownership, then resumes writing. An in-memory simulation makes expiry and
competing clients repeatable.

## Goal and activities

Separate command atomicity, lock ownership and stale-writer rejection.
You will reproduce races using a supplied in-memory stand-in for Redis and a manual
clock, not contact Redis or establish exactly-once effects in a live system.
Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Only standard-library simulation code runs; dependencies are in `pyproject.toml`.

A lease grants ownership until a deadline. An owner token identifies who may
release the lock. A fencing token serves another purpose: each new owner gets
a larger number, and the destination rejects writes carrying an older number.
Expiry alone cannot make a paused process forget its old permission.

1. Install and compare `INCR` with separate client reads and writes below.
2. Run `pytest -q tests/test_lab_26_distributed_lock.py`. Trace the old owner
   releasing a new owner's lock after expiry.
3. Compare naive deletion, compare-and-delete and the fenced destination.
   Only the destination's token check rejects the resumed stale writer.
4. Advance the manual clock through scheduler restart and inspect the retained
   interval ledger. Then explain what process-memory loss would remove.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

Run the configured checks before considering the lab complete:

```bash
pybootstrap check
```

Add `-v` to include warnings, `--gate lint` to run one gate, and `--fix` to
apply the corrections tools can make on their own.

Each gate can also be run directly, because pybootstrap does not wrap or
reconfigure them:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Tests

`tests/test_lab_26_distributed_lock.py` holds the unit tests. They check
single pieces such as the Redis command model, the release paths, the fenced
store and the scheduler ledger, each with the manual clock.

`tests/test_functional.py` holds the functional tests. They drive the
composed simulation through the package's public classes: competing workers
take turns on the lock for `task-17`, a paused worker is fenced out while the
current owner moves the job to `succeeded`, and a scheduler cluster runs each
interval once across a restart.

Run each kind alone with `pytest tests/test_lab_26_distributed_lock.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate ran and found problems |
| 2 | A gate could not run and supplied no verdict |

Fix findings reported by exit 1. For exit 2, repair the tool or its
configuration and rerun it; an unavailable check cannot establish a pass.

The dev extra installs the public pybootstrap project and its gate tools from
GitHub, so a fresh clone receives the same quality runner used by every lab.

## Simulation focus

The package first compares atomic Redis commands with client-side sequences:

- two clients lose an update when each performs `GET` followed by `SET`
- two server-side `INCR` commands preserve both updates
- a parameterized Lua release script is cached by SHA-1, executes
  compare-and-delete in one server turn, and reloads after `NOSCRIPT`

It then models three coordination mechanisms with an in-memory clock:

- Redis-style `SET NX PX` plus both naive and compare-delete release paths
- a fenced lease manager that issues monotonic ownership tokens
- an in-memory scheduler ledger that deduplicates modeled interval decisions

Tests reproduce the client-command race and naive `DEL` bug, show the paused-holder
corruption on an unfenced store, reject the same stale writer with fencing, and
store one schedule per interval while that ledger is retained, without live
Redis or Azure. This is not an exactly-once guarantee for external job effects.

Use destination fencing when a resumed old holder could corrupt state.
An expiring Redis lock alone is suitable only when duplicate work is tolerable.

## Layout

```
src/lab_26_distributed_lock/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Python REPL debugging session

After the editable install, inspect lease and fencing values:

```pycon
>>> import inspect
>>> import lab_26_distributed_lock as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> store = lab.InMemoryRedisStore(lab.SimulationClock())
>>> store.counter_incr("relay:attempts", client_id="worker-a")
1
>>> store.counter_incr("relay:attempts", client_id="worker-b")
2
>>> [event.command for event in store.events]
['INCR', 'INCR']
```

Each `INCR` is one modeled server operation, so both increments are preserved.
Compare that with the test that interleaves two `GET`/`SET` pairs and loses an
update. The lease tests then apply the same distinction to releasing a lock.

The SigRaft job-orchestration web service must reject writes from workers
after their leases are no longer current. This lab demonstrates that check, not a
lock package imported by Lab 39. Finish with stale-write rejection demonstrated
and `pybootstrap check` exit 0. No subscription or server is needed. Exit the
REPL to discard lock, script-cache and ledger state.
