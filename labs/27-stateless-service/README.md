# Lab 27 Stateless Service

This lab moves task storage out of each FastAPI application instance
and behind a repository interface. Two instances can then read and change the
same tasks without maintaining separate copies.

## Goal and activities

Show that application instances can share one repository while keeping their
own readiness and drain state. You will drive two supplied ASGI applications
in process and test conditional writes, not deploy two network hosts.
Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
FastAPI/Pydantic validate HTTP requests and HTTPX/TestClient sends test requests;
all dependencies are declared in `pyproject.toml`. No Azure account is needed.

An ETag identifies a stored version. Sending it back in `If-Match` asks the
server to apply a change only if that version is still current. This prevents
an old client view from silently overwriting a newer update.

1. Install and create a repository-backed app in the REPL.
2. Run `pytest -q tests/test_lab_27_stateless_service.py`. Submit through one
   client and read through the other, which shares the same repository object.
3. Patch with the returned ETag, then reuse the old ETag and expect 412.
   Omit `If-Match` and expect 428 rather than an unconditional overwrite.
4. Inspect the drain test and distinguish a live process from an instance
   ready to accept new requests.

## Relay task contract

Labs 27 through 29 store tasks with these fields:

- `tenant_id`
- `task_id`
- `title`
- `status`
- `payload`
- `depends_on`
- `etag` or version metadata

This is not byte-for-byte the earlier `id/action/state` API. Here `title`
and `payload` describe work and `status` names its lifecycle. A real integration
must translate these fields explicitly; do not rename the actual API in an
example merely to hide the difference.

This lab adds:

- `POST /tasks`
- `GET /tasks` and `GET /tasks/{tenant_id}/{task_id}`
- `PATCH /tasks/{tenant_id}/{task_id}`
- optimistic concurrency with `ETag` and `If-Match`
- separate `/livez` and `/readyz`
- graceful drain state so readiness can fail before shutdown

The tests create a task through one app and read it through another. Both use
the same fake repository instead of storing tasks in each app.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

You can run the tools on their own too:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Tests

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

`tests/test_repository.py` holds the unit tests. They check
`InMemoryTaskRepository` on its own: duplicate creation, ETag changes and
stale ETags, tenant filtering, missing jobs and an unready repository.

`tests/test_lab_27_stateless_service.py` holds the functional tests. They
send HTTP requests through `TestClient` to one or two FastAPI applications
built by `create_app`. They check shared storage across instances,
conditional `PATCH` with `If-Match`, separate liveness and readiness, and
draining while a request is still in flight.

```bash
pytest tests/test_repository.py
pytest tests/test_lab_27_stateless_service.py
```

`pybootstrap check` runs both kinds of test in its test gate.

## Python REPL debugging session

After the editable install, inspect the service and repository interfaces:

```pycon
>>> import inspect
>>> import lab_27_stateless_service as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> from fastapi.testclient import TestClient
>>> repository = lab.InMemoryTaskRepository()
>>> app = lab.create_app(repository=repository)
>>> with TestClient(app) as client:
...     response = client.post("/tasks", json={
...         "tenant_id": "tenant-a", "task_id": "task-17", "title": "Inspect jobs",
...     })
>>> response.status_code, response.json()["status"]
(201, 'queued')
```

The response reports a newly created queued job. The repository, rather than
the application instance, stores its data. Follow the two-client test next:
a second app can retrieve it because both apps receive the same repository.

## Contribution and completion

This lab separates HTTP handling from storage for the SigRaft
job-orchestration web service. Lab 39 does not import this repository.
The shared fake demonstrates reuse inside one process, not cross-process
durability or a deployed database. Finish with conditional writes and drain
behavior demonstrated, and `pybootstrap check` exit 0. Exit 1 means
findings; exit 2 means a gate could not run. Close TestClient and discard the
in-memory repository; there are no remote resources to destroy.
