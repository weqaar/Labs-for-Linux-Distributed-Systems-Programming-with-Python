# Lab 26 Airflow Dags

Checkpoint 26 of the relay product. This lab keeps the relay DAG from Lab 25
but describes it in a form that imports fast and does not require Airflow to be
installed.

## Relay DAG

The task ids stay aligned with the engine checkpoint:

- `discover-pending-tasks`
- `hydrate-task-context`
- `run-relay-task`
- `persist-task-status`
- `publish-run-metrics`

This checkpoint adds:

- a UTC daily schedule
- catchup and bounded backfill rules
- a deferrable upstream sensor
- worker-pool resource semantics
- a serialisable Airflow blueprint adapter

The module-level DAG build performs no network access and constructs no client.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
