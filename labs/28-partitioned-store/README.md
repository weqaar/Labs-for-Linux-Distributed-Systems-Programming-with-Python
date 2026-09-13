# Lab 28 Partitioned Store

Checkpoint 28 of the relay product. This lab moves the same relay task contract
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
## Python REPL debugging session

After the editable install, inspect partition and replica objects:

```pycon
>>> import inspect
>>> import lab_28_partitioned_store as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Inspect the partition-key function signature and one placement result before
simulating a node loss or rebalance.
