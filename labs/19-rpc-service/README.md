# Lab 19 Rpc Service

This checkpoint turns the relay `/tasks` contract into a small HTTP RPC layer,
and adds a real FastAPI application that serves the same contract over ASGI.
The fake service and client stay in memory for tests, but they keep the
failure modes that matter:

- request correlation with `x-correlation-id`
- deadlines sent as remaining budget in `x-relay-budget-ms`
- idempotency keys for `POST /tasks`
- replay cache for dropped replies and duplicated requests
- a fake transport that can drop, delay and duplicate calls

The FastAPI application in `api.py` exposes the same `/tasks` resource, task
IDs and states over a real ASGI boundary:

- `TaskSubmissionBody` validates request shape and types with Pydantic; the
  domain rules from `TaskSubmission`, such as the `task-<positive integer>`
  ID pattern and a UTC timestamp, run afterwards and can reject a body
  Pydantic accepted
- `RelayTaskState` reuses `ReplayCache` from `rpc.py`, so a retry carrying the
  same idempotency key and body gets the same cached reply whether the
  transport in front of it is the fake service or a real ASGI app
- every route is `async def`; none of them do blocking work, so none of them
  need `asyncio.to_thread`

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
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

## Notes

- `TaskSubmission` and `TaskStatus` keep the same `/tasks` contract as Lab 16.
- `RelayRpcClient` enforces the deadline at the caller, not only at the server.
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
  `RELAY_USE_UVLOOP` from the environment. It resolves the event loop name
  through Uvicorn's own `loop=` setting rather than calling
  `uvloop.install()` itself: leaving `RELAY_USE_UVLOOP` unset runs on
  asyncio, and asking for uvloop when it is not installed raises
  `UvloopUnavailableError` instead of quietly falling back. Every worker,
  one or many, is started from the same `lab_19_rpc_service.api:app_factory`
  import string with Uvicorn's `factory=True`, which is the one target valid
  whether Uvicorn starts a single process or several; each worker process
  Uvicorn starts calls the factory itself and gets its own `RelayTaskState`,
  so scaling past one worker needs an external store for tasks and the
  replay cache, not more copies of this in-memory one.
- `ServerConfig` bounds its own inputs: the port must be between 1 and
  65535, the host must not be empty, the worker count must be positive and
  at most `MAX_WORKERS`, so an environment typo cannot spawn an unbounded
  number of processes, and the import string must not be empty either.
  `RELAY_USE_UVLOOP` is parsed against a fixed set of true and false
  spellings; a value outside that set, a typo, raises rather than silently
  parsing as false.
- `api.py` bounds the correlation ID, idempotency key and remaining-budget
  headers, and the task ID, target and timestamp fields, so an oversized or
  malformed request is rejected with a 400 or 422 rather than an unbounded
  allocation or a 500. A blank, all-whitespace, correlation ID or
  idempotency key is rejected the same way a missing one is, rather than
  being stored as a usable identifier. The timestamp bound runs as a
  Pydantic "before" validator on the raw string, since Pydantic's `datetime`
  field parses that string directly and would otherwise never see the
  shared contract's length limit.
## Python REPL debugging session

After the editable install, inspect the RPC boundary:

```pycon
>>> import inspect
>>> import lab_19_rpc_service as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Inspect request, reply, deadline, and adapter signatures before simulating a
lost response. Keep invocation separate from import-time inspection.
