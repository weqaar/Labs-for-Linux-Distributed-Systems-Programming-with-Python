# Lab 05 Azure Resources

This checkpoint adds the first Azure infrastructure model for the same relay
product. The code stays pure Python and offline, but it plans the resource
group, storage account, task container, task queue and managed identity that
later `relayctl` and `/tasks` checkpoints will use.

## Capability added here

- model the desired Azure state for relay resources
- prove a second apply has no changes when the first one completed
- plan teardown in reverse dependency order so the resource group can be
  destroyed cleanly
- keep `Contributor` separate from blob and queue data-plane roles

Example product shape:

```text
relayctl submit --task-id task-17 --action rebuild-search-index
relay stores the task document in blobs and pushes work onto the tasks queue
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
