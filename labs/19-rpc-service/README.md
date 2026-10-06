# Lab 19 Rpc Service

This lab submits and reads tasks through HTTP. A remote procedure call
(RPC) asks another component to perform an operation and return a result.
The lab provides both a fake transport for controlled failures and a FastAPI
application. FastAPI uses ASGI, the interface between an asynchronous Python
application and a server such as Uvicorn.

The fake path makes these request behaviors observable:

- request correlation with `x-correlation-id`
- deadlines sent as remaining budget in `x-relay-budget-ms`
- idempotency keys for `POST /tasks`
- replay cache for dropped replies and duplicated requests
- a fake transport that can drop, delay and duplicate calls

The FastAPI application in `api.py` exposes the same `/tasks` resource, task
IDs and states through ASGI:

- `TaskSubmissionBody` validates request shape and types with Pydantic; the
  domain rules from `TaskSubmission`, such as the `task-<positive integer>`
  ID pattern and a UTC timestamp, run afterwards and can reject a body
  Pydantic accepted
- `RelayTaskState` reuses `ReplayCache` from `rpc.py`, so a retry carrying the
  same idempotency key and body gets the same cached reply whether the
  transport in front of it is the fake service or a real ASGI app
- every route is `async def`; none of them do blocking work, so none of them
  need `asyncio.to_thread`

## Goal and working order

Make a remote submission distinguish transport success, domain acceptance and
an unknown outcome after a lost reply. You will inspect the supplied fake RPC
path and real ASGI application, inject failures and optionally start Uvicorn.
Accepted jobs remain in memory; this lab does not execute their actions.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
FastAPI routes requests, Pydantic validates input and Uvicorn serves the app.
The default loop is asyncio. HTTPX/TestClient and gate tools are development dependencies,
all declared in `pyproject.toml`. No Azure subscription or remote service is
needed. The fake clock tests do not measure production latency.

1. Install below and perform the in-process request in the REPL.
2. Run `pytest -q tests/test_lab_19_rpc_service.py tests/test_api.py`.
   Trace a dropped reply through the replay cache and compare a duplicate
   body with a conflicting body under the same key.
3. Run `pytest -q tests/test_concurrency.py tests/test_server.py`.
   Compare offloaded blocking work with blocking the event loop.
4. Optionally run `RELAY_HOST=127.0.0.1 RELAY_PORT=8080 RELAY_WORKERS=1 python -m lab_19_rpc_service.server`.
   Use the same request headers as the REPL and stop the foreground server
   with Ctrl-C. Several workers would have several independent stores.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

uvloop is an optional extra, not a default dependency:

```bash
pip install -e ".[dev,uvloop]"
```

## Quality gates

```bash
pybootstrap check
```

The same checks can be run one by one:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Follow a request through the implementation

Begin with the request models, then follow the service call and transport.
The components below keep input validation, retry decisions and process
configuration in separate places.

- `TaskSubmission` and `TaskStatus` use the task fields developed in
  Lab 18; Lab 16's framing is a separate byte-transport exercise.
- `RelayRpcClient` and the fake RPC service enforce the modeled deadline.
  The FastAPI routes validate and record `x-relay-budget-ms`, but do not
  measure elapsed time or enforce an end-to-end deadline. Carrying a budget
  header is not the same as enforcing it.
- `RelayHttpService` replays the cached answer when a retry carries the same
  idempotency key and the same request body.
- `create_app()` builds an ASGI application exercised in tests with
  `fastapi.testclient.TestClient`, which drives requests in process over the
  same scope, receive and send interface Uvicorn would use. No socket is
  opened and no uvloop installation is required.
- `concurrency.py` demonstrates the blocking-work trap directly:
  `blocking_write` stalls a `Heartbeat` running on the same event loop, and
  `offloaded_write` does the same work on a thread without stalling it.
- `server.py` reads `RELAY_HOST`, `RELAY_PORT`, `RELAY_WORKERS` and
  `RELAY_USE_UVLOOP` from the environment. It selects Uvicorn's `loop=`
  setting: asyncio by default, or uvloop when explicitly requested.
  Requesting an unavailable uvloop raises `UvloopUnavailableError`.
  With `factory=True`, each worker calls
  `lab_19_rpc_service.api:app_factory` and gets its own `RelayTaskState`.
  Multiple workers therefore need shared storage for both tasks and replayed
  replies; their in-memory dictionaries are independent.
- `ServerConfig` requires a port between 1 and 65535, a nonblank host and
  import string, and between one and `MAX_WORKERS` processes. The worker cap
  prevents a mistyped count from starting arbitrarily many processes.
  `RELAY_USE_UVLOOP` accepts only its documented Boolean spellings; a typo
  raises an error rather than silently selecting asyncio.
- `api.py` limits correlation IDs, idempotency keys, remaining-budget headers,
  task IDs, targets and timestamp strings. The tested violations produce
  400 or 422 responses. Blank correlation IDs and idempotency keys are rejected;
  omitting an idempotency key is allowed. A Pydantic "before" validator checks
  timestamp length because conversion to `datetime` loses the raw string length.

These field checks do not limit the entire HTTP body, stored task count or
replay-cache size. A submission without a matching replay entry overwrites an
existing task with the same ID. The budget parser also accepts `"00"` as zero
and uses `isdigit()`, which admits some characters that `int()` cannot parse.
Treat these as limits of the supplied example, not production validation
guarantees.

## Python REPL debugging session

After the editable install, submit a request to the in-process ASGI application:

```pycon
>>> import inspect
>>> import lab_19_rpc_service as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> from fastapi.testclient import TestClient
>>> app = lab.create_app()
>>> with TestClient(app) as client:
...     response = client.post(
...         "/tasks",
...         headers={"x-correlation-id": "corr-17", "x-relay-budget-ms": "100"},
...         json={"id": "task-17", "action": "index", "target": "documents",
...               "submitted_at": "2026-08-18T05:52:44Z"},
...     )
>>> response.status_code, response.json()["state"]
(202, 'queued')
```

The 202 response means the request was accepted; `queued` says the action has
not run. TestClient exercised the application without opening a socket.
Now compare this successful request with the dropped-reply test, where the
client must decide whether repeating the request is safe.

## Contribution and completion

This teaches HTTP, ASGI, deadlines and idempotency for the SigRaft
job-management web service. Lab 39 retains this lab's test results but uses
its own HTTP server, not this FastAPI application.
Finish when duplicate handling, deadline exhaustion and invalid request
behavior are demonstrated, and `pybootstrap check` exits 0. Exit 1 means
findings; exit 2 means a gate could not run. Close TestClient, stop any live
server, and deactivate the environment. In-memory tasks are not durable.
