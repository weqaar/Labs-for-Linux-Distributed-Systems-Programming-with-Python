# Lab 15 Zeromq Patterns

This checkpoint models the relay task service with the three ZeroMQ shapes used
in the chapter:

- PUSH/PULL work distribution with drain on shutdown
- PUB/SUB status fan-out with high-water drop accounting
- DEALER/ROUTER commands with recoverable request IDs

The tests use in-memory queues so they stay deterministic. They still show the
facts that matter: a slow subscriber can lose messages, a late subscriber starts
with a known gap, and a REQ-style caller wedges when a reply is lost while a
DEALER-style caller can recover by request ID.

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
