# Lab 18 Validated Models

Development and check instructions for this lab.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means at least one gate
could not produce a verdict. Other gates may still have useful results.
Inspect the diagnostics, repair the affected check and rerun it before
reporting success.

## Layout

```
src/lab_18_validated_models/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Request and status models forbid extra fields. Event models may keep unknown
  fields for forward compatibility.
- Boundary code uses `model_validate_json` or `model_validate_strings`, never
  `model_construct`.
- Relay timestamps are timezone aware and in UTC.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, hiding the failed check from the pipeline.
