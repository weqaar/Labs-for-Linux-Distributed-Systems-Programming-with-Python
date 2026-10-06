# Lab 08 Azure Resources

Checkpoint 08 models the Azure resources intended to support relay's
`/tasks` API. Its offline planner compares desired resources with in-memory
state; separate Bicep files support optional live deployments.

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

- plan relay resource group, storage, queue and identity state offline
- check that a second model apply makes no changes and deletion reverses dependencies
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
  build, hiding the failed check from the pipeline.
