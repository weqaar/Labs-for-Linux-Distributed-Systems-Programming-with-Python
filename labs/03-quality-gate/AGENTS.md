# Lab 03 Quality Gate

Checkpoint 03 records quality-check results for relay and `relayctl`.
It distinguishes a reported finding from a checker error in exit codes and
JUnit output.

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

- model pass, fail and error outcomes for relay quality gates
- emit JUnit XML that Azure Pipelines can publish with separate failures and errors
- keep tests offline, deterministic and strict about bad invocations

## Layout

```
src/lab_03_quality_gate/    the package
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
