# Lab 24 Raft Election

## Goal and activities

Explain how terms, voting rules and a majority select a leader under a chosen
failure schedule. You will advance a supplied simulation, not start Raft
processes or configure a production cluster.
Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Raft is a protocol that elects one leader and copies an ordered log. The
simulation uses standard-library values and in-memory state; all development
dependencies are declared in `pyproject.toml`.

A term identifies an election period. A follower may become a candidate after
its timeout, but becomes leader only after receiving a majority of votes.
Remembering a vote prevents the same node from voting for two candidates in
one term.

1. Install and trigger an election in the REPL.
2. Run `pytest -q tests/test_lab_24_raft_election.py`. Trace a vote across
   `restart_node`, then a higher term forcing step-down.
3. Partition four nodes into two pairs, trigger each timeout, heal the
   partition and trigger a later timeout. Neither pair alone has a majority.
4. Change the timeout order in a test and explain which observations, rather
   than elapsed wall time, caused the result.

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

The package models follower, candidate and leader roles over a scripted network.
Tests choose the election order by selecting which node's timeout
fires next. They cover:

- one vote per term retained across a simulated node restart
- split-vote recovery on a later timeout
- higher-term step-down
- minority partitions failing to elect a leader

The restart reuses a Python `PersistentVoteState`; it does not fsync a disk
record or survive loss of the Python process. No claim about a managed Azure
service's internal consensus implementation follows from this simulation.

## Layout

```
src/lab_24_raft_election/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Python REPL debugging session

After the editable install, inspect node state:

```pycon
>>> import inspect
>>> import lab_24_raft_election as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> cluster = lab.DeterministicRaftCluster(("n1", "n2", "n3"))
>>> result = cluster.trigger_timeout("n1")
>>> result.became_leader, result.term
(True, 1)
>>> cluster.node("n1").role is lab.NodeRole.LEADER
True
```

The selected timeout starts term one and enough nodes grant votes for `n1`
to become leader. No wall-clock wait caused that result. Use a partitioned
test next to see the same timeout fail when a majority cannot be reached.

These elections show why a SigRaft job-orchestration web service needs a
majority before choosing a coordinator. Lab 39 does not import this cluster
or run consensus merely by listing it in release records.
Finish with demonstrated split-vote recovery and higher-term step-down, and
`pybootstrap check` exit 0. Exit Python to discard the simulated nodes; no
network or Azure resources were created.
