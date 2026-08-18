# Lab 06 Azure Sdk

This checkpoint adds the first real Azure data-plane boundary for the relay
product. The code builds blob and queue adapters that a later HTTP service
and `relayctl` can use without knowing Azure client details.

## Capability added here

- create relay blob and queue adapters behind typed protocols
- inject a `DefaultAzureCredential` compatible credential instead of using
  connection strings
- surface retry-safe `ServiceRequestError` and idempotent
  `ServiceResponseError` decisions without adding extra retry loops
- list task documents lazily by page with offline fake SDK clients

Example product shape:

```text
relayctl submit --task-id task-17 --action rebuild-search-index
relay writes /tasks/task-17 to blob storage and enqueues task-17 for workers
```

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

Direct commands stay the same locally and in CI:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
