# Lab 24 Raft Election

Deterministic Raft leader-election checkpoint for the relay task service.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

The same command runs on a laptop and in CI, so a failure is always
reproducible:

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
| 2 | A gate could not run, so nothing was checked |

The split between 1 and 2 is the point. A missing or misconfigured tool is not
the same as clean code, and a pipeline that treats them alike will eventually
report success while checking nothing.

The dev extra installs the public pybootstrap project and its gate tools from
GitHub, so a fresh clone receives the same quality runner used by every lab.

## Simulation focus

The package models follower, candidate and leader roles over a scripted network.
Tests drive the randomised election outcome by choosing which node's timeout
fires next. They cover:

- one durable vote per term, even across restart
- split-vote recovery on a later timeout
- higher-term step-down
- minority partitions failing to elect a leader

For a production relay scheduler on Azure, I would still deploy a managed lease
for single-leader selection. This lab keeps Raft because it teaches the stronger
mechanism the managed service is built on.

## Layout

```
src/lab_24_raft_election/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.
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
```

Construct the smallest cluster, inspect one node's term and role, and inspect
the election-step signature before advancing the deterministic schedule.
