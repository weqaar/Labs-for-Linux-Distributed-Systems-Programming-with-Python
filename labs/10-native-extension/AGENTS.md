# Lab 10 Native Extension

Orientation for anyone, human or AI, working in this repository.

## Checkpoint role

This lab is the `relay` framing-hot-loop checkpoint. Keep the standalone
service name as `relay`, the future CLI name as `relayctl`, and task records
shaped as `task_id`, `definition`, and `state` with the states `queued`,
`running`, `succeeded`, and `failed`.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Layout

```
src/lab_10_native_extension/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
