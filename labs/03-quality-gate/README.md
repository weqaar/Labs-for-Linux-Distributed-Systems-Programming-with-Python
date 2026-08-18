# Lab 03 Quality Gate

This checkpoint adds honest quality gate evidence for the same relay
product. It models the outcomes that protect `relayctl` and the `/tasks`
service from a lying pipeline: pass, fail and error.

## Capability added here

- classify a relay gate as passed, failed or errored
- render JUnit XML with `<failure>` for real findings and `<error>` when a
  tool never ran
- keep the overall exit code honest so `error` outranks `failure`
- publish the same evidence shape that Azure Pipelines can gate on

Example relay-focused flow:

```text
pybootstrap check --junit-dir .quality
relayctl submit --task-id task-17 --action rebuild-search-index
```

Azure Pipelines can then publish `.quality/*.xml` and fail closed on both
failures and errors.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

Direct commands stay the same locally and in CI:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
