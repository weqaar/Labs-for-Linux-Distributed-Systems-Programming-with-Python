# Lab 24 Kv Store

Checkpoint 24 of the relay product. This lab keeps the same relay task contract
and models the Cosmos access pattern around it without calling a real service.

## Relay task contract

Labs 22 to 26 keep the same task fields:

- `tenant_id`
- `task_id`
- `title`
- `status`
- `payload`
- `depends_on`
- `etag` or version metadata

This checkpoint adds:

- a defensible partition key of `tenant_id`
- conditional writes with ETags
- fixed point-read request accounting
- higher-cost cross-partition query accounting
- explicit TTL and indexing policies

The rejected partition keys are documented in code and tests. `status` was
rejected because it hot-spots queued work, and `task_id` was rejected because
tenant-scoped queries would fan out.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
