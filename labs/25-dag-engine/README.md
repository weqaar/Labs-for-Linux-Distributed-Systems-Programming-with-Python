# Lab 25 Dag Engine

Checkpoint 25 of the relay product. This lab turns the relay task flow into a
deterministic DAG engine on `graphlib`.

## Relay DAG

The engine keeps the relay workflow shape used again in Lab 26:

- `discover-pending-tasks`
- `hydrate-task-context`
- `run-relay-task`
- `persist-task-status`
- `publish-run-metrics`

This checkpoint adds:

- cycle reporting before any work starts
- deterministic ready ordering
- bounded parallel scheduling
- descendant skipping after failure
- retryable node behavior without replaying successful branches

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
