# Lab 38 Resource Scheduling

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
src/lab_38_resource_scheduling/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Placement decisions must be deterministic. Stable ties sort by node id.
- Queues transport committed placements; they do not choose a node.
- Tests use supplied inventory and integer time. They do not inspect or mutate
  the host's cgroups, CPU affinity, NUMA policy or GPU configuration.
- Only the elected scheduler may reserve resources, and reservation must be
  recorded before dispatch.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
