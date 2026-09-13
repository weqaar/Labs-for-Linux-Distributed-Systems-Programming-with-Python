# Lab 27 Stateless Service

Checkpoint 27 of the relay product. This lab exposes the stable relay task
contract over HTTP with one stateless FastAPI app instance at a time or many in
parallel.

## Relay task contract

Every checkpoint in Labs 22 to 26 keeps the same task shape:

- `tenant_id`
- `task_id`
- `title`
- `status`
- `payload`
- `depends_on`
- `etag` or version metadata

This checkpoint adds:

- `POST /tasks`
- `GET /tasks` and `GET /tasks/{tenant_id}/{task_id}`
- `PATCH /tasks/{tenant_id}/{task_id}`
- optimistic concurrency with `ETag` and `If-Match`
- separate `/livez` and `/readyz`
- graceful drain state so readiness can fail before shutdown

The tests run two app instances against one shared fake repository to prove
there is no instance-local task state.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
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
## Python REPL debugging session

After the editable install, inspect service and repository boundaries:

```pycon
>>> import inspect
>>> import lab_27_stateless_service as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Inspect one conditional-update signature and immutable task value before
running requests through two service instances.
