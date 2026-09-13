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

Read the shared [`CODING_STANDARDS.md`](../../CODING_STANDARDS.md) first. It
defines the type, object-design, validation, error-handling, resource and test
rules used by every checkpoint.

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
## Python REPL debugging session

After the editable install, inspect the package actually loaded by Python:

```pycon
>>> import inspect
>>> import lab_01_first_service as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> [(name, type(getattr(lab, name)).__name__) for name in public]
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Select one public callable, inspect its signature, then construct the smallest
valid relay object and inspect its `type`, `repr`, and public attributes. Do not
call every name returned by `dir()`: discovery does not prove a call is safe.

## Checking the data boundary

Chapter 1 asks you to verify a data boundary rather than assume one, whether
the agent reading your code is hosted or local. This checkpoint gives a small,
checkable example of that idea: `dependencies = []` in `pyproject.toml` is a
claim that nothing here can reach a network, and the claim can be checked in
the same REPL session rather than taken on trust.

```pycon
>>> import ast, inspect
>>> import lab_01_first_service as lab
>>> source = inspect.getsource(lab.relay)
>>> tree = ast.parse(source)
>>> imported = sorted(
...     {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
...     | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
... )
>>> imported
```

The result is `['__future__', 'dataclasses', 'enum', 're']`: no socket, no
`http`, no third-party client. That is what makes this checkpoint's offline
claim in `AGENTS.md` a checked boundary instead of an assertion, and it is the
same discipline Chapter 1 asks you to apply to any coding agent, local or
hosted, before trusting what it says about where your data goes.
