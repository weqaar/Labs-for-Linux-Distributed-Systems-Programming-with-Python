# Lab 08 Azure Resources

Checkpoint 08 of the relay product. This lab replaces the greeting scaffold
with an offline desired-state planner for the Azure resources behind relay's
`/tasks` API.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Checkpoint focus

- plan relay resource group, storage, queue and identity state offline
- prove repeat apply is idempotent and teardown order is safe
- keep control-plane and data-plane RBAC separate in the model
- keep Bicep split into a resource-group main file and focused storage and
  platform modules
- require dev, staging and production parameter files with no credentials
- preserve OIDC workload identity, AcrPull, blob and queue role separation
- keep live Azure deployment optional and outside the deterministic gate

## Layout

```
src/lab_08_azure_resources/    the package
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
