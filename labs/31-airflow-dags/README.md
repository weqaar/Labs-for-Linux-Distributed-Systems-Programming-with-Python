# Lab 31 Airflow directed acyclic graphs

This lab describes SigRaft's workflow from Lab 30 as data an Airflow adapter
could use. Its Python import name is `relay`; the program does not relay
traffic. Importing the blueprint does not require Airflow.

## Goal and activities

Describe scheduling intent separately from runtime orchestration. You will
inspect a supplied serializable blueprint, calculate logical run dates and
test resource declarations. This lab does not construct Airflow graph objects,
run a scheduler, execute sensors or import the named runtime callables.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Dataclasses and UTC datetime arithmetic come from the standard library.
Airflow is not a declared dependency. Keep dependencies in `pyproject.toml`
if you later implement a separately scoped integration.

A blueprint describes what an Airflow adapter would need to create. Backfill
means requesting past scheduled runs; its cap prevents one request from
creating an unbounded backlog. A sensor waits for a prerequisite, such as a
partition snapshot. Marking it deferrable asks a future runtime to release its
worker slot while it waits.

1. Install and inspect the blueprint in the REPL.
2. Run `pytest -q tests/test_lab_31_airflow_dags.py` and compare the 02:00 UTC
   schedule with the seven-run backfill cap.
3. Inspect `wait-for-partition-snapshot`, the extra sensor preceding Lab 30's
   workflow. Explain why a deferred sensor need not occupy a worker slot.
4. Change a resource or retry declaration in a test and inspect serialized
   output. Treat `callable_name` strings as proposed adapter targets, not
   runnable functions: the referenced `runtime` module is not supplied.

## Relay DAG

The task ids stay aligned with the engine lab:

- `discover-pending-tasks`
- `hydrate-task-context`
- `run-relay-task`
- `persist-task-status`
- `publish-run-metrics`

This lab adds:

- a UTC daily schedule
- catchup and bounded backfill rules
- a declaration for a deferrable upstream sensor
- worker-pool resource declarations
- a serialisable Airflow blueprint adapter

The module-level DAG build performs no network access and constructs no client.

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

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

`tests/test_lab_31_airflow_dags.py` holds the unit tests. They check the
schedule fields, the backfill cap, resource declarations, the blueprint size
and an import with network calls blocked.

`tests/test_functional.py` holds the functional tests. They drive the public
composition function `build_relay_dag()` the way an Airflow adapter would.
They serialize the blueprint to JSON, order its tasks from the sensor to the
metrics step, request a backfill after a long outage, and check the errors for
a local-time window and an unknown task.

```bash
pytest tests/test_lab_31_airflow_dags.py
pytest tests/test_functional.py
```

`pybootstrap check` runs both kinds of test in its test gate.

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
>>> blueprint = lab.RELAY_DAG.as_airflow_blueprint()
>>> blueprint["schedule"], len(blueprint["tasks"])
('0 2 * * *', 6)
>>> lab.RELAY_DAG.task("wait-for-partition-snapshot").deferrable
True
```

The cron expression means 02:00 every day; this blueprint uses UTC. Its six
tasks consist of the five workflow steps and the extra snapshot sensor.
`True` marks a deferral request, not proof that a sensor has run.
Inspect dependencies and callable names before attempting an Airflow adapter.

## Contribution and completion

This blueprint describes a proposed daily workflow for the SigRaft
job-orchestration web service, not an Airflow dependency imported by Lab 39.
Finish when you can explain UTC logical dates, bounded backfill and the missing
runtime adapter, with `pybootstrap check` exit 0. Exit 1 means findings;
exit 2 means a gate could not run. No subscription, scheduler process or
database is needed. Exit the REPL to discard blueprint objects.
