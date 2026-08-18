# Lab 16 Websocket Service

This checkpoint adds a relay event stream with sequence IDs and replay. The
service code stays in memory for tests, but it keeps the protocol rules that
matter:

- replay from `resume_after` so reconnects lose no events
- keepalive pings before idle infrastructure drops the socket
- bounded outbound queue with a clear slow-client close
- broker fan-out so one publish reaches connections on other instances
- reconnect backoff with deterministic jitter so clients do not bunch up

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
