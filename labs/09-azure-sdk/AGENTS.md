# Lab 09 Azure Sdk

Checkpoint 09 implements typed Azure blob and queue adapters for relay task
documents. Tests inject fake SDK clients; the lab does not run an HTTP service.

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

- inject `DefaultAzureCredential` compatible credentials
- keep blob and queue code behind typed protocols and fake SDK clients
- distinguish request loss, response loss and permission errors precisely

## Layout

```
src/lab_09_azure_sdk/    the package
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
