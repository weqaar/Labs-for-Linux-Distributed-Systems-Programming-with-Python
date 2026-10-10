# Lab 18 Validated data

This lab uses Pydantic v2 to check incoming data before the service accepts
it. A model, in this lab, is a Python class that lists the allowed fields
and rejects anything else. The code calls a job a task. The import name is
`relay`. That name does not mean the program relays traffic. The classes
cover a request, a job status and an event for the later messaging labs:

- `POST /tasks` accepts `id`, `action`, `target`, `submitted_at`
- `GET /tasks/{id}` returns `id`, `action`, `target`, `state`, timestamps
- events carry `sequence`, `id`, `action`, `state`, `timestamp`, `detail`

Request and status models forbid extra fields. Event models keep unknown fields so
new publishers do not break older readers. Every timestamp must be timezone aware
and in UTC.

## Goal and activities

Reject malformed input before calling service methods so they receive validated
values. This package provides those classes and an in-memory service, not an
HTTP listener. The route descriptions above name the intended contract.
It contributes validation design to the SigRaft job-management web service;
Lab 39 defines separate models rather than importing this lab.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Pydantic v2 is the runtime dependency; all tools are declared in
`pyproject.toml`. No subscription, environment credentials or live server is
needed for the local gate.

1. Install and parse the REPL request through `model_validate_json`.
2. Add an unknown field to that JSON and expect `ValidationError`.
   Compare that policy with a `TaskEvent`, which preserves unknown fields.
3. Read the timezone validator and the sanitized error response builder.
   Compare a UTC timestamp, a missing offset and a non-UTC offset.
4. Run `pytest -q tests/test_lab_18_validated_models.py`. Add a boundary
   rejection case without bypassing validation through `model_construct`.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
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

## Tests

The lab has two kinds of test. `tests/test_lab_18_validated_models.py` holds
the unit tests. They check each model, validator, settings loader and error
builder on its own.

`tests/test_functional.py` holds the functional tests. They drive the public
package the way a `/tasks` HTTP adapter would, passing raw JSON bodies through
the boundary models into `RelayTaskService`. They check that a job moves from
`queued` through `running` to `succeeded` or `failed`, that a rejected body
gets a 422 response and creates no job, and that a rejected transition leaves
the job status unchanged.

Run each kind alone with `pytest tests/test_lab_18_validated_models.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Validate before using a value

Decoding JSON tells you which values arrived, not whether they are acceptable.
The model's validation step checks field types, allowed actions and timestamps
before the in-memory service receives a task.

- Use `model_validate_json` or `model_validate_strings` at the boundary.
- Do not use `model_construct` or raw dictionaries inside the service code.
- `validation_error_response()` returns a 422 body without echoing the bad input.
- `boundary_json_schemas()` exposes the JSON Schema for the shared relay models.
## Python REPL debugging session

After the editable install, inspect model fields and validation:

```pycon
>>> import inspect
>>> import lab_18_validated_models as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> submission = lab.TaskSubmission.model_validate_json(
...     '{"id":"task-17","action":"index","target":"documents",'
...     '"submitted_at":"2026-08-18T05:52:44Z"}'
... )
>>> submission.id, submission.action.value
('task-17', 'index')
>>> type(submission), submission.model_dump()
```

The accepted object contains an action enum and parsed UTC timestamp, not just
the original strings. Add an extra field to the same JSON and inspect the
resulting validation error. That comparison shows where untrusted input stops
and a validated application value begins.

Finish when you can separate type hints from runtime validation, explain extra
field policy and inspect a schema without constructing unsafe input.
`pybootstrap check` must exit 0; exit 1 reports findings and exit 2 means a
gate could not run. Exit Python to discard in-memory objects; there is no
infrastructure to clean up.
