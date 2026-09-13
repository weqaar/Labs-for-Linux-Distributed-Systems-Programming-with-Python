# Lab 25 Replicated Log

Deterministic replicated-log checkpoint for the relay task service.

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

The package extends election into a replicated relay task log and deterministic
state machine. Tests cover:

- majority-only commit
- ordered application of committed commands
- follower catch-up after missing the whole write sequence
- restart from persisted log state
- an isolated leader's uncommitted entry never becoming visible

For a production deployment I would still store the durable log in a managed
service rather than operate raw disks on app instances. The point here is the
commit boundary, not the storage product.

## Layout

```
src/lab_25_replicated_log/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.
## Python REPL debugging session

After the editable install, inspect log entries and node state:

```pycon
>>> import inspect
>>> import lab_25_replicated_log as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Inspect one log entry's type, term, index, and command before applying it.
Compare an appended entry with a committed and applied entry.
