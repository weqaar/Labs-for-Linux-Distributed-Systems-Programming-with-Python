# Lab 14 Rpc Service

This checkpoint turns the relay `/tasks` contract into a small HTTP RPC layer.
The service and the client stay in memory for tests, but they keep the failure
modes that matter:

- request correlation with `x-correlation-id`
- deadlines sent as remaining budget in `x-relay-budget-ms`
- idempotency keys for `POST /tasks`
- replay cache for dropped replies and duplicated requests
- a fake transport that can drop, delay and duplicate calls

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

## Notes

- `TaskSubmission` and `TaskStatus` keep the same `/tasks` contract as Lab 13.
- `RelayRpcClient` enforces the deadline at the caller, not only at the server.
- `RelayHttpService` replays the cached answer when a retry carries the same
  idempotency key and the same request body.
