# Lab 04 Cli Tool

Orientation for anyone, human or AI, working in this repository.

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
src/lab_04_cli_tool/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Typer command functions render results; `RelayClient` owns HTTP, timeouts,
  retries and service error mapping.
- Tests inject `httpx.MockTransport` and a sleep function. They must not open a
  real socket or wait for a retry delay.
- A GET may be retried within the policy budget. Do not add retries around
  mutating requests without an idempotency key.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
