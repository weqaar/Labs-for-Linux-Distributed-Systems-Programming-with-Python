# Lab 31 Airflow Dags

Checkpoint 31 of the relay product. This lab keeps the relay DAG from Lab 30
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
## Python REPL debugging session

Inspect the import-safe blueprint before requiring Airflow:

```pycon
>>> import inspect
>>> import lab_31_airflow_dags as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Inspect task IDs, dependencies, schedule fields, and callable signatures on the
blueprint before translating them into Airflow objects.
