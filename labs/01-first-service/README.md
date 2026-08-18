# Lab 01 First Service

This checkpoint turns the scaffold into the first relay service core. It is
still offline and in memory, but it already uses the product vocabulary that
later chapters keep: task IDs like `task-17`, actions, states
`queued/running/succeeded/failed`, and the REST path `/tasks` that
`relayctl` will target.

## Capability added here

- submit a task definition into relay
- read task status back from `/tasks/<task-id>`
- move work from `queued` to `running` to `succeeded` or `failed`
- reject bad task IDs, missing tasks and impossible state changes with
  explicit domain errors

Example shape for the later CLI:

```text
relayctl submit --task-id task-17 --action rebuild-search-index
relayctl status task-17
```

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

Direct commands stay the same locally and in CI:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
