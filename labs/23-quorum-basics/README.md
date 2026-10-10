# Lab 23 Quorum Basics

## Goal and activities

Determine when a read set must intersect a completed write set, and observe
what happens when it need not. This is an in-memory quorum-register simulation
for the SigRaft job-orchestration web service, not a networked replicated
database. Its centrally assigned versions and scripted writes are assumptions;
the results do not prove that concurrent real reads always behave like reads
from one up-to-date copy.

A replica holds one copy of a value. `N` is the number of replicas, `W` is the
number required to acknowledge a write, and `R` is the number consulted by a
read. A quorum is the required set of participants. If `R + W > N`, those
read and write sets cannot be disjoint.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The runtime uses standard-library dataclasses and dictionaries; dependencies
are declared in `pyproject.toml`. No external services are needed.

1. Install and inspect quorum arithmetic in the REPL.
2. Read the five-node fixtures and run
   `pytest -q tests/test_lab_23_quorum_basics.py`.
3. Trace contacted nodes for the latest and stale reads. Keep `N` fixed while
   changing `R` and `W`; predict intersection before running.
4. Inspect a minority partition's failed write and the majority's successful
   write. Explain the availability cost of requiring enough replies.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

Run the configured checks before considering the lab complete:

```bash
pybootstrap check
```

Add `-v` to include warnings, `--gate lint` to run one gate, and `--fix` to
apply the corrections tools can make on their own.

Each gate can also be run directly, because pybootstrap does not wrap or
reconfigure them:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate ran and found problems |
| 2 | A gate could not run and supplied no verdict |

Fix findings reported by exit 1. For exit 2, repair the tool or its
configuration and rerun it; an unavailable check cannot establish a pass.

The dev extra installs the public pybootstrap project and its gate tools from
GitHub, so a fresh clone receives the same quality runner used by every lab.

## Simulation focus

The package models five relay replicas, a logical version clock and explicit
network partitions. Tests demonstrate:

- latest reads when `R + W > N`
- deliberate stale reads when `R + W <= N`
- a minority partition that stalls writes while the majority continues
- why four replicas tolerate the same single failure as three

No live Azure hosts, sockets or sleeps are required. Every scenario is driven by
deterministic test input.

## Tests

`tests/test_lab_23_quorum_basics.py` holds the unit tests. They check quorum
arithmetic and single read and write scenarios, including a stale read from
disjoint quorums and an unknown job.

`tests/test_functional.py` holds the functional tests. They drive a five-node
`QuorumRegisterCluster` through its public methods and follow the status of
`task-17` from `queued` to `running` to `completed`. They check that every
majority read returns the latest write, that a minority partition refuses a
write while the majority continues, that healing the partition restores the
latest read, and that reading a job never written is rejected.

Run `pytest tests/test_lab_23_quorum_basics.py` for the unit tests alone and
`pytest tests/test_functional.py` for the functional tests alone.
`pybootstrap check` runs both.

## Layout

```
src/lab_23_quorum_basics/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Python REPL debugging session

After the editable install, inspect the simulation types:

```pycon
>>> import inspect
>>> import lab_23_quorum_basics as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> cluster = lab.QuorumRegisterCluster(
...     node_ids=("n1", "n2", "n3"), clock=lab.SimulationClock(),
... )
>>> cluster.node_count, lab.majority_quorum(3)
(3, 2)
>>> inspect.signature(cluster.read_task)
>>> written = cluster.write_task(
...     task_key="relay:task:17",
...     record=lab.RelayTaskRecord("task-17", "default", lab.RelayTaskStatus.QUEUED),
...     coordinator="n1", write_quorum=2, preferred_nodes=("n1", "n2"),
... )
>>> result = cluster.read_task(
...     task_key="relay:task:17", coordinator="n1", read_quorum=2,
...     preferred_nodes=("n2", "n3"),
... )
>>> result.value == written
True
```

The write reaches `n1` and `n2`; the read consults `n2` and `n3`. Their shared
replica, `n2`, carries the written version into the read. Next inspect the
five-node test that deliberately chooses disjoint sets and returns stale data.

Finish when you can explain stale observations and quorum unavailability, and
`pybootstrap check` exits 0. Use these observations to evaluate how many replicas
must answer a SigRaft read or write; Lab 39 does not import this store.
Exit Python to discard all replica state;
there are no hosts or disks to tear down.
