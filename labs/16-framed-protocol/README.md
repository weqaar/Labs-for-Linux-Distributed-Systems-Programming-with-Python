# Lab 16 Framed Protocol

Relay checkpoint fourteen replaces raw byte streams with a bounded framed
transport.

## Checkpoint role

- service name: `relay`
- CLI name: `relayctl`
- task fields: `task_id`, `definition`, `state`
- task states: `queued`, `running`, `succeeded`, `failed`

This checkpoint adds a 4-byte big-endian length prefix, a maximum frame size,
an incremental reader that survives arbitrary chunk boundaries, a partial-write
sender, and reuse of the same reader logic for blob chunks as well as sockets.
The reader now stores incomplete data in a fixed-capacity ring buffer. Tests
force head and tail wraparound, reject overflow atomically, and drain many
frames from an input chunk larger than the ring.

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

## Layout

```
src/lab_16_framed_protocol/    the package
tests/                the test suite
ci/                   pipeline definition
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.
## Python REPL debugging session

After the editable install, inspect the framing objects:

```pycon
>>> import inspect
>>> import lab_16_framed_protocol as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Inspect one encoder and decoder signature, then follow a payload through the
ring buffer while recording its type and length at each boundary.
