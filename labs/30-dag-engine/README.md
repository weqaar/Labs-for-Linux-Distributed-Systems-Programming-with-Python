# Lab 30 Dag Engine

Checkpoint 30 of the relay product. This lab turns the relay task flow into a
deterministic DAG engine on `graphlib`.

## Relay DAG

The engine keeps the relay workflow shape used again in Lab 31:

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
```

Construct the smallest DAG, inspect its node and dependency values, and compare
its topological order with the AST traversal from Lab 10.
