# Lab 22 Logical Clocks

This lab orders task updates when machines disagree about the time.
Wall time is a calendar timestamp. Logical clocks instead advance when
events occur or messages arrive, so their ordering does not depend on matching
machine clocks. Each update keeps both forms:

- Lamport stamps provide a deterministic total order
- vector clocks detect whether two updates are ordered or concurrent
- task updates serialise with a stable JSON form
- a skewed wall clock can place a later event before its cause
- Pendulum parses explicit-offset input, normalizes storage to UTC, and renders
  an operator's IANA timezone

Pendulum parses and displays calendar timestamps only. Relay still uses a monotonic
clock for timeout budgets and vector clocks for causal order.

## Goal and activities

Separate operator timestamps, elapsed-time budgets and causal order. You will
step supplied clock objects directly, not synchronize machines or deploy a
replicated store. Comparing these clocks helps distinguish timestamps from
causal order in the SigRaft job-orchestration web service; Lab 39 does not
import these classes.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Dataclasses represent clock values, JSON serializes updates, and Pendulum
handles civil time. Dependencies are declared in `pyproject.toml`.

1. Install and advance two Lamport clocks in the REPL.
2. Run `pytest -q tests/test_lab_22_logical_clocks.py`. Compare the skewed
   wall-time sort with the causal relation and logical order.
3. Reproduce two concurrent updates, merge their vectors and explain why
   concurrency does not select a winner.
4. Change an input timezone offset and inspect the normalized UTC value.
   Keep deadline calculations independent of civil time.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
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

## Tests

`tests/test_lab_22_logical_clocks.py` holds the unit tests. They check the
Lamport clock, the vector clock, update serialization and the Pendulum
timestamp helpers one at a time.

`tests/test_functional.py` holds the functional tests. They drive the public
package interface the way an operator tool would: parse operator timestamps,
exchange `task-17` updates between two `RelayReplica` objects, and order,
serialize and display the results. They check that a skewed wall clock
misorders the `queued`, `running` and `succeeded` updates while logical order
keeps them in causal order, that conflicting outcomes are reported as
concurrent, and that bad timestamps and job identifiers are rejected.

Run `pytest tests/test_lab_22_logical_clocks.py` for the unit tests alone and
`pytest tests/test_functional.py` for the functional tests alone.
`pybootstrap check` runs both.

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
>>> west, east = lab.LamportClock("west"), lab.LamportClock("east")
>>> sent = west.local_event()
>>> received = east.observe(sent)
>>> sent.counter, received.counter
(1, 2)
>>> inspect.signature(east.observe)
```

The receiving clock advances beyond the received counter, placing receipt
after send without consulting wall time. That ordering alone cannot tell
whether two unrelated updates influenced one another. The vector-clock test
keeps a counter per participant to identify that distinction.

Finish when you can explain that Lamport order does not prove causality in
reverse, while vector comparisons can identify concurrency.
`pybootstrap check` must exit 0; exit 1 means findings and exit 2 means a
gate could not run. No network or Azure account is used; exit Python to
discard clocks and histories.
