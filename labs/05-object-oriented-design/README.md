# Lab 05 Object-oriented design

This lab separates a job's data, its storage and the code that performs
its action. Immutable `Task` objects prevent accidental changes. A repository
interface describes storage operations, and handlers implement a common
operation for different actions.

## Goal and setup

Learn which objects own state and which interfaces let implementations change.
You start with a working reference service, inspect it, then extend its tests
and handlers. It runs in memory and synchronously, not as an HTTP listener or
background job system.

Use Python 3.10 or later from this directory. Read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The runtime uses standard-library dataclasses, enums, protocols, descriptors
and decorators; no vendor SDK is needed.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q tests/test_lab_05_object_oriented_design.py
```

All dependencies, including development tools, belong in `pyproject.toml`.

## Build the lab

Begin with the REPL session below to create `task` and `service`. The following
investigations explain why those objects behave differently from plain
dictionaries and functions.

1. At a Python prompt, construct a `Task`. Inspect `type(task)`,
   `isinstance(task, object)`, `id(task)`, `repr(task)`, and the public names
   from `dir(task)`. Confirm that `type(Task) is type`.
2. Compare `service.submit` with `type(service).submit`. Inspect
   `service.submit.__self__`, `service.submit.__func__`, and both signatures to
   see how Python binds `self`.
3. Read `models.py` and identify which invariants belong to construction,
   properties, and the `PositiveInteger` descriptor.
4. Follow `TaskRepository` into the in-memory adapter. Confirm that the service
   depends on the protocol and receives its repository through construction.
5. Add a new action and handler. The metaclass registry should discover it
   without adding a conditional branch to `TaskService`.
6. Inspect `type(handler).__mro__`, `inspect.signature(service.submit)`, and the
   read-only handler registry.
7. Run the complete gate:

```bash
pybootstrap check
```

The lab is complete when invalid domain values cannot enter the
repository, returned collections cannot mutate service state, decorator
metadata remains inspectable, every action uses the common handler operation,
and all gates exit zero.

SigRaft's job-management web service also separates job values, service
operations and storage. Here, `TaskRepository` defines the methods the service
calls, and the in-memory adapter implements them. This independent package is
not imported by Lab 39. A production storage adapter would implement those
methods without moving SDK-specific code into task objects.

## Tests

The lab has two kinds of test. `tests/test_lab_05_object_oriented_design.py`
holds the unit tests. They check single objects in isolation, such as the
`Task` value, the descriptor, the decorator, the handler registry and the
repository adapter.

`tests/test_functional.py` holds the functional tests. They drive the service
returned by `build_default_service` through `submit`, `run`,
`counts_by_state` and `audit_events`. They check that jobs reach `succeeded`
through the registered handlers, that a duplicate `task-17` or an unknown job
is rejected, and that a handler exception reaches the caller and leaves the
job `running`.

Run one kind alone with `pytest tests/test_lab_05_object_oriented_design.py`
or `pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Python REPL debugging session

```pycon
>>> import inspect
>>> import lab_05_object_oriented_design as lab
>>> lab.__name__, lab.__file__
>>> task = lab.Task("task-17", lab.TaskAction.INDEX, "documents")
>>> type(task), isinstance(task, object), repr(task)
>>> [name for name in dir(task) if not name.startswith("_")]
>>> service = lab.build_default_service()
>>> service.submit(task).state.value
'queued'
>>> service.run("task-17").state.value
'succeeded'
>>> service.submit.__self__ is service
True
>>> service.submit.__func__ is type(service).submit
True
>>> inspect.signature(service.submit)
>>> help(type(task))
```

This session separates the object, its class, its public attributes, and the
bound method that supplies `self`. The two `True` results show that the method
keeps both its owning instance and the underlying class function; Python
supplies the instance when you call it.

Inspect `TaskService.run` before adding failure behavior: the supplied method
does not catch handler exceptions and mark the task failed, so an exception
leaves the stored task running. `Task.with_state` copies a value without
checking transition order, and `run` can run a terminal task again. Immutability
protects a snapshot from mutation; it does not enforce a complete lifecycle.

On completion, explain immutable snapshots, structural protocols, method
binding and decorator metadata. `pybootstrap check` must exit 0; exit 1
reports findings and exit 2 reports a gate that could not run. Exit the REPL
and deactivate the environment. In-memory tasks and audit events disappear
with the process; there are no external resources to destroy.
