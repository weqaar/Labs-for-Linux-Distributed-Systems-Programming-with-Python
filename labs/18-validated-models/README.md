# Lab 18 Validated Models

This checkpoint puts strict Pydantic v2 models on the relay task boundaries.
It keeps one `/tasks` contract across the later labs:

- `POST /tasks` accepts `id`, `action`, `target`, `submitted_at`
- `GET /tasks/{id}` returns `id`, `action`, `target`, `state`, timestamps
- events carry `sequence`, `id`, `action`, `state`, `timestamp`, `detail`

Request and status models forbid extra fields. Event models keep unknown fields so
new publishers do not break older readers. Every timestamp must be timezone aware
and in UTC.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
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

## Notes

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
```

Build one valid and one invalid input at the prompt. Inspect the valid object's
type and representation and the invalid exception's type and structured detail.
