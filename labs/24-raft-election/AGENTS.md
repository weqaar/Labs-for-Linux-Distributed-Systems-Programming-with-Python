# Lab 24 Raft Election

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
src/lab_24_raft_election/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
- Keep elections deterministic. Tests should choose timeout order directly
  rather than waiting on wall-clock randomness.
- Install the public pybootstrap project and its gate tools through the lab's
  dev dependency.
