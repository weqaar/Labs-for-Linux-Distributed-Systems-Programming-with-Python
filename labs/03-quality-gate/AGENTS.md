# Lab 03 Quality Gate

Checkpoint 03 of the relay product. This lab replaces the greeting scaffold
with the quality gate evidence that proves relay and `relayctl` were really
checked, and tells `failure` apart from `error` in JUnit output.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Checkpoint focus

- model pass, fail and error outcomes for relay quality gates
- emit JUnit XML that Azure Pipelines can publish honestly
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
  build, which is worse than no gate at all because it looks like coverage.
