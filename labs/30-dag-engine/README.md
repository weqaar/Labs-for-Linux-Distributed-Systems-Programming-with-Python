# Lab 30 Directed acyclic graph engine

This lab continues SigRaft. The import name is `relay`. That name does not
mean the program relays traffic. This lab turns the job flow into a directed
acyclic graph. A directed acyclic graph, abbreviated DAG, is a set of steps
with arrows that never loop back. The implementation uses Python's
`graphlib`.

## Goal and activities

Decide when a step may run, retry or be skipped. You will inspect the graph
and simulate outcomes using supplied durations. The engine advances integer
time and stores each attempt; it does not start threads, subprocesses or
job handlers.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
`graphlib.TopologicalSorter` supplies dependency bookkeeping and `heapq`
orders modeled completion events. All dependencies are in `pyproject.toml`;
no orchestration platform or subscription is needed.

Each graph node represents a workflow step; an edge means one step depends on
another. A ready step has no unfinished prerequisites. It may still have to
wait for a parallel slot, and its dependants cannot proceed if it fails.

1. Install and run the supplied workflow in the REPL.
2. Run `pytest -q tests/test_lab_30_dag_engine.py`. Inspect ready ordering
   and the maximum number of modeled in-flight attempts.
3. Insert a back edge in a test and expect `CycleDetectedError` before any
   attempt. Compare failure propagation with an independent branch.
4. Supply a retry followed by success and verify already successful branches
   are not replayed.

## Workflow steps

The engine uses these SigRaft workflow steps, also described in Lab 31.
The code keeps the `task` and `relay` names in the step identifiers:

- `discover-pending-tasks`
- `hydrate-task-context`
- `run-relay-task`
- `persist-task-status`
- `publish-run-metrics`

This lab adds:

- cycle reporting before any work starts
- deterministic ready ordering
- bounded simulated parallel scheduling
- descendant skipping after failure
- retryable node behavior without replaying successful branches

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
## Python REPL debugging session

After the editable install, inspect workflow nodes and edges:

```pycon
>>> import inspect
>>> import lab_30_dag_engine as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> engine = lab.DagEngine(lab.build_relay_workflow(), max_parallel=2)
>>> inspect.signature(engine.run)
>>> result = engine.run({})
>>> all(item.state is lab.NodeState.SUCCEEDED for item in result.node_results.values())
True
```

All nodes report success because an empty outcome mapping uses the model's
default successful attempt, not a real handler invocation. Follow the retry
test next to supply a failure explicitly and inspect how it changes the
attempt history and dependent nodes.

## Contribution and completion

This lab shows how dependencies and retries affect job ordering in the
SigRaft job-orchestration web service. Lab 31 describes the same workflow plus
a sensor; Lab 39 does not import this engine.
Finish when you can distinguish dependency readiness from priority and real
execution from a simulated attempt, with `pybootstrap check` exit 0. Exit 1
means findings; exit 2 means a gate could not run. Exit Python to discard
the run history. No workers or external resources are started.
