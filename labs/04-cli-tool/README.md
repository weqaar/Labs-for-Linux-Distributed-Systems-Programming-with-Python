# Lab 04 CLI Tool

`relayctl status` asks a web service for a job's current state. Typer handles
command-line arguments; an HTTPX client makes the request. The client sets
timeouts and retry limits, and the command translates the result into output
and an exit code.

## Goal and purpose

Make an operator command fail predictably when the service times out,
returns malformed data or reports a missing job. This lab provides a
status client, not a server or a submit command. It contributes the CLI,
configuration and bounded read retries to the SigRaft job-management web service;
the final `sigraftctl` is a separate implementation.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Typer parses commands, HTTPX sends HTTP requests and supplies mock transports,
and the standard library reads configuration. Dependencies are declared in `pyproject.toml`.
Use a separate environment because Lab 02 also installs a command named
`relayctl` with different behavior.

1. Install, inspect `settings.py`, and run the help and config commands below.
2. Inspect the retry policy in the REPL. Run
   `pytest -q tests/test_lab_04_cli_tool.py` and trace the injected timeout,
   503 response and success. The delays the test captures must be 0.25 and 0.5 seconds;
   the test does not actually sleep.
3. Change a response in a local test to 404 and verify exit 1, then to invalid
   JSON and verify a command error. Do not add retries to writes without an
   idempotency contract.

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

## Layout

```
src/lab_04_cli_tool/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```


## Try the command

```bash
relayctl --help
relayctl config
```

Help lists the available commands. Config shows where settings came from
without printing the token. Neither command needs a running job service.

The following optional calls require a separately running compatible service
and an existing `task-17`. This lab does not start that service. The default
test path uses fakes and requires neither credentials nor Azure.

```bash
RELAY_TOKEN=development relayctl --url http://127.0.0.1:8080 status task-17
RELAY_TOKEN=development relayctl --url http://127.0.0.1:8080 status task-17 --json
```

The tests use `typer.testing.CliRunner` and `httpx.MockTransport`. They exercise
the command and REST client together without opening a socket. The retry test
injects a timeout, a 503 and a successful response, replaces sleep with a list,
and asserts the exact exponential delays.

The bounds apply to HTTP operations and retry count, not total elapsed command
time. This client does not interpret `Retry-After` or expose a public close
method. Those are additional requirements for a long-lived client, not
features demonstrated by this command.

## Tests

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

`tests/test_lab_04_cli_tool.py` holds both kinds. The unit tests are
`test_settings_follow_documented_precedence` and
`test_retry_policy_rejects_invalid_bounds`. They call `resolve_settings` and
`RetryPolicy` directly. Every other test in the module is a functional test.
Each one invokes the `relayctl` Typer app with `CliRunner` and checks the exit
code, stdout and stderr. Behind the command, the real `RelayClient` talks to
`httpx.MockTransport`, so the tests cover status output, a missing job, a
missing token, retries after a timeout and a 503, and malformed replies.

```bash
pytest tests/test_lab_04_cli_tool.py -k "precedence or retry_policy"
pytest tests/test_lab_04_cli_tool.py -k "not precedence and not retry_policy"
```

`pybootstrap check` runs both kinds of test in its test gate.

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
>>> from lab_04_cli_tool.client import RetryPolicy
>>> policy = RetryPolicy(attempts=3, base_delay=0.25, max_delay=1.0)
>>> inspect.signature(policy.delay)
```

The policy object stores retry limits; constructing it sends no request.
Follow `RelayClient.task` and the `status` callback to see where those limits
are used and where the result becomes command output.

## Completion and cleanup

Finish when you can explain settings precedence, redaction, transport errors
and retry limits, and `pybootstrap check` exits 0. The gate exit codes above
describe checker results; the CLI has its own error mapping tested separately.
No listener or cloud resource is created by the fake tests. Deactivate this
environment before moving to another lab's `relayctl`.
