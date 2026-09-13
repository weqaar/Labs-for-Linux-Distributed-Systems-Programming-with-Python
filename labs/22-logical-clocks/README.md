# Lab 22 Logical Clocks

This checkpoint adds Lamport and vector clocks to relay task updates. Each
update keeps both wall time and logical time so the tests can show the split:

- Lamport stamps provide a deterministic total order
- vector clocks detect whether two updates are ordered or concurrent
- task updates serialise with a stable JSON form
- a skewed wall clock can place a later event before its cause
- Pendulum parses explicit-offset input, normalizes storage to UTC, and renders
  an operator's IANA timezone

Pendulum handles the civil-time boundary only. Relay still uses a monotonic
clock for timeout budgets and vector clocks for causal order.

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
## Python REPL debugging session

After the editable install, inspect clock values and operations:

```pycon
>>> import inspect
>>> import lab_22_logical_clocks as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Construct two logical clock values, inspect their immutable state, and compare
their merge or ordering operation without consulting wall time.
