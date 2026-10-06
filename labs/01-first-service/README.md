# Lab 01 First service

## Goal and purpose

Learn how a job moves through a small, explicit lifecycle before adding a
network, a command-line client or persistent storage. The supplied reference
implementation is the first in-memory stage of SigRaft. The import name
in this lab is `relay`. That name does not mean the program relays traffic.

You will inspect the implementation, submit a job, change its state,
and test rejected operations. You are not starting an HTTP server or running
`relayctl` here. The code stores a path such as `/tasks/task-17` on the
Python object that holds the job status. That path is a field, not a
listening network address. An action such as `rebuild-search-index` is stored
text; this lab does not execute it.

A job definition says what work is requested. A job status says where that
work is in its lifecycle. The code calls the status object `TaskRecord`.
That is a Python object, not a database record and not a file. Keeping the
definition and the status separate lets you test a change of state without
a worker that performs the work.

## Prerequisites and setup

Use Python 3.10 or later and a shell in `labs/01-first-service`. Read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and this lab's `AGENTS.md`.
Create an isolated environment so later labs' independently installed packages
and commands do not replace this lab.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Python dataclasses group the fields of a job definition and a job status.
An enum gives the states fixed names. The service stores each job status in
a dictionary keyed by the job identifier. In this code that identifier looks
like `task-17`. There are no runtime third-party packages.
Ruff, Pyright, pytest and pybootstrap are development tools declared in
`pyproject.toml`. Installing them can require network access; running the
core and its tests needs no Azure subscription or external service.

An empty dependency list does not prove absence of networking. Python's
standard library can open sockets. Inspecting `relay.py` shows what this
implementation actually does: validate values and update its own dictionary.

## Python REPL debugging session

1. Read `src/lab_01_first_service/relay.py` and
   `tests/test_lab_01_first_service.py`. Locate validation in `submit` and
   the state check used by `start_task`, `succeed_task` and `fail_task`.
2. Start `python` after the editable install. This opens the interactive prompt,
   also called the REPL. Enter the lines after `>>>`, without copying the prompt
   itself. Inspect the installed package before calling its API.

```pycon
>>> import inspect
>>> import lab_01_first_service as lab
>>> lab.__name__, lab.__file__
>>> inspect.signature(lab.InMemoryRelayService.submit)
>>> service = lab.InMemoryRelayService()
>>> definition = lab.TaskDefinition("task-17", "rebuild-search-index")
>>> type(definition), repr(definition)
>>> queued = service.submit(definition)
>>> queued.state.value, queued.resource_path
('queued', '/tasks/task-17')
>>> service.start_task("task-17").state.value
'running'
>>> service.succeed_task("task-17", detail="exercise complete").state.value
'succeeded'
>>> service.get_status("task-17").detail
'exercise complete'
>>> queued.state.value
'queued'
```

The earlier status object remains queued because a transition returns a new
object instead of changing the old one. The current status lives in the
service. A new `InMemoryRelayService()` has an empty `list_tasks()` tuple;
nothing is recovered from disk.

3. Submit `task-18`, start it, then call
   `service.fail_task("task-18", "exercise failure")`. Observe `failed` and
   the reason. Try submitting `task-17` again, querying `task-99`, and
   completing a newly queued task without starting it. Expect
   `TaskAlreadyExists`, `TaskNotFound` and `InvalidTaskTransition`,
   respectively. These are deliberate errors, not successful operations.
4. Run the behavioral tests. Add a test that a blank failure reason is
   rejected and the running job remains unchanged. Keep the implementation's
   lifecycle rather than bypassing validation to make the test pass.

```bash
pytest -q tests/test_lab_01_first_service.py
pybootstrap check
```

## Contribution and completion

SigRaft uses the same job identifiers, actions, resource paths and
`queued`, `running`, `succeeded` or `failed` states. The code writes
identifiers as `task-17` and paths as `/tasks/task-17`. Practising submission and
state changes here lets you recognize those operations when later labs
add transports and storage adapters. Lab 39 has its own implementation; it
does not need to import this package.

Finish when you can distinguish a job definition from a job status, explain why
an invalid transition fails without changing state, and demonstrate both
terminal outcomes. `pybootstrap check` must exit 0 after every configured
gate runs. Exit 1 means a gate found a problem; exit 2 means a gate could not
run and supplied no verdict.

Exit Python with `exit()` and deactivate the environment with `deactivate`.
There are no listeners, cloud resources or saved jobs to remove.
