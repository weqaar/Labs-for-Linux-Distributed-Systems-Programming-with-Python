# Lab 01 First Service

Checkpoint 01 implements an offline, in-memory stage of SigRaft. The import
name is `relay`. It stores job submission and status changes before later
labs add a CLI and the HTTP `/tasks` API.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means at least one gate
could not produce a verdict. Other gates may still have useful results.
Inspect the diagnostics, repair the affected check and rerun it before
reporting success.

## Checkpoint focus

- submit jobs with source identifiers such as `task-17`
- expose `/tasks/<task-id>` as a record's resource path, not a live endpoint
- keep state in memory with explicit domain errors
- stay small, typed and offline so later checkpoints can build on it

## Layout

```
src/lab_01_first_service/    the package
tests/                the test suite
```

## Commands

```bash
pybootstrap check
pytest
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, hiding the failed check from the pipeline.
