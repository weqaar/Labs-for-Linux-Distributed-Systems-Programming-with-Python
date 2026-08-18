# Lab 23 Partitioned Store

Checkpoint 23 of the relay product. This lab moves the same relay task contract
from one stateless service repository to a consistent-hash replicated store.

## Relay task contract

Labs 22 to 26 keep the same task identity and payload fields:

- `tenant_id`
- `task_id`
- `title`
- `status`
- `payload`
- `depends_on`
- `etag` or version metadata

This checkpoint adds:

- stable hashing with virtual nodes
- configurable `N`, `R`, and `W`
- distribution and movement measurements
- immutable versions
- surfaced concurrent siblings instead of clock-based resolution

The tests prove that the ring gives the same answers in a second Python
process, so the placement logic does not depend on the process-local hash seed.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
