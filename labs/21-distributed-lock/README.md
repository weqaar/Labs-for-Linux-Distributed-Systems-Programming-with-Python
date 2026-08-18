# Lab 21 Distributed Lock

Deterministic lock and lease checkpoint for the relay scheduler.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

The same command runs on a laptop and in CI, so a failure is always
reproducible:

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

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate ran and found problems |
| 2 | A gate could not run, so nothing was checked |

The split between 1 and 2 is the point. A missing or misconfigured tool is not
the same as clean code, and a pipeline that treats them alike will eventually
report success while checking nothing.

The dev extra installs the public pybootstrap project and its gate tools from
GitHub, so a fresh clone receives the same quality runner used by every lab.

## Simulation focus

The package models three coordination mechanisms with an in-memory clock:

- Redis-style `SET NX PX` plus both naive and compare-delete release paths
- a fenced lease manager that issues monotonic ownership tokens
- an exactly-once scheduler ledger keyed by interval

Tests prove the naive `DEL` bug, show the paused-holder corruption on an
unfenced store, reject the same stale writer with fencing, and schedule exactly
once per interval without live Redis or Azure.

For relay correctness I would deploy the fenced mechanism. Redis with expiry is
acceptable when duplicate work is merely wasteful, not when an old holder can
corrupt state.

## Layout

```
src/lab_21_distributed_lock/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.
