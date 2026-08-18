# Lab 17 Logical Clocks

This checkpoint adds Lamport and vector clocks to relay task updates. Each
update keeps both wall time and logical time so the tests can show the split:

- Lamport stamps provide a deterministic total order
- vector clocks detect whether two updates are ordered or concurrent
- task updates serialise with a stable JSON form
- a skewed wall clock can place a later event before its cause

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

The same checks can be run one by one:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
