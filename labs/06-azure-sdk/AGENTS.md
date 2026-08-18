# Lab 06 Azure Sdk

Checkpoint 06 of the relay product. This lab replaces the greeting scaffold
with typed Azure blob and queue adapters behind protocol boundaries for the
same relay `/tasks` service.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Checkpoint focus

- inject `DefaultAzureCredential` compatible credentials
- keep blob and queue code behind typed protocols and fake SDK clients
- distinguish request loss, response loss and permission errors precisely

## Layout

```
src/lab_06_azure_sdk/    the package
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
