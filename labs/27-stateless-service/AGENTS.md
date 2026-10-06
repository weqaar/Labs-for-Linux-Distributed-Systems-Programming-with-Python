# Lab 27 Stateless Service

Development and review guidance for this lab.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means at least one gate
could not produce a verdict. Other gates may still have useful results.
Read the gate output, repair the failed tool or configuration, and rerun.

## Layout

```
src/lab_27_stateless_service/    the package
tests/                the test suite
```

## Relay checkpoint

This lab is checkpoint 27 of the relay service. Keep the task fields named
the same way as later labs: `tenant_id`, `task_id`, `title`, `status`,
`payload`, `depends_on`, and `etag` or version metadata.

Tests must stay offline and deterministic.

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
