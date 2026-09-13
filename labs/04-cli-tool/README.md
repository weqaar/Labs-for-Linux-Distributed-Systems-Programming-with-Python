# Lab 04 CLI Tool

`relayctl` is a Typer command that calls a REST service through an injected
HTTPX transport. It owns explicit timeouts, a bounded retry policy and the
mapping from HTTP outcomes to command exit codes.

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
src/lab_04_cli_tool/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.

## Try the command

```bash
relayctl --help
relayctl config
RELAY_TOKEN=development relayctl --url http://127.0.0.1:8080 status task-17
RELAY_TOKEN=development relayctl --url http://127.0.0.1:8080 status task-17 --json
```

The tests use `typer.testing.CliRunner` and `httpx.MockTransport`. They exercise
the command and REST client together without opening a socket. The retry test
injects a timeout, a 503 and a successful response, replaces sleep with a list,
and asserts the exact exponential delays.
## Python REPL debugging session

After the editable install, inspect the package actually loaded by Python:

```pycon
>>> import inspect
>>> import lab_04_cli_tool as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> [(name, type(getattr(lab, name)).__name__) for name in public]
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Inspect the CLI construction callable and one command callback before invoking
the same behavior through the command-line parser.
