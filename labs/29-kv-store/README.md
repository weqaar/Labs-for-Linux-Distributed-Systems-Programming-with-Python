# Lab 29 Kv Store

This lab models how SigRaft could store jobs in Azure Cosmos DB. Its Python
import name is `relay`; the program does not relay traffic. The fake client
performs no Azure requests.

## Goal and activities

Choose a partition key from access patterns, then test conditional updates
and expiry without paying for a database. The supplied `FakeCosmosClient`
uses fixed request-unit costs and a controllable clock. Those costs teach a
comparison; they are not measurements or prices from Azure Cosmos DB.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The implementation uses the standard library, not the Cosmos SDK. Dependencies
are declared in `pyproject.toml`; no subscription or endpoint is required.

A point read supplies both the partition and item key. A query without the
partition key may have to inspect several partitions. Request units represent
database work, not elapsed time. The fake assigns fixed costs so this
difference is visible without treating a benchmark as a pricing estimate.

An ETag identifies a record version for conditional updates. Time to live
(TTL) limits how long a record remains available; the fake clock lets you test
that limit immediately.

1. Install and inspect the container policy in the REPL.
2. Run `pytest -q tests/test_lab_29_kv_store.py` and compare point-read,
   tenant-query and cross-partition accounting.
3. Patch with the current ETag, then retry with the original one and expect
   `ConditionalWriteFailedError`.
4. Advance `FixedClock` past a test record's TTL and observe
   `TaskNotFoundError`. Do not wait for real elapsed time.

## Relay task contract

Labs 27 through 29 use these storage-oriented fields:

- `tenant_id`
- `task_id`
- `title`
- `status`
- `payload`
- `depends_on`
- `etag` or version metadata

They use `title/payload/status` rather than the earlier `action/state` wire
representation. A service adapter must make that translation explicitly.

This lab adds:

- a `tenant_id` partition key for tenant-scoped reads
- conditional writes with ETags
- fixed point-read request accounting
- higher-cost cross-partition query accounting
- explicit TTL and indexing policies

The rejected partition keys are documented in code and tests. `status` was
rejected because it hot-spots queued work, and `task_id` was rejected because
tenant-scoped queries would fan out.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
## Python REPL debugging session

After the editable install, inspect keys, records, and repository methods:

```pycon
>>> import inspect
>>> import lab_29_kv_store as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> model = lab.CosmosTaskRepository.default_container_model()
>>> model.partition_key_path, model.ttl_policy.default_ttl_seconds
('/tenant_id', 86400)
>>> inspect.signature(lab.CosmosTaskRepository.patch_task)
```

The policy groups records by tenant and supplies a default lifetime of
86,400 seconds, or one day. These are configuration values, not a database
created by the REPL. Follow the expiry test to see how the repository applies
that policy when its clock advances.

## Contribution and completion

Use this model to compare partition choices and prevent stale updates in the
SigRaft job-orchestration web service; Lab 39 does not import this fake
repository or gain Cosmos persistence from it.
Finish with partition, ETag and expiry tests demonstrated and
`pybootstrap check` exit 0. Exit 1 means findings; exit 2 means a gate could
not run. Exit Python to discard all modeled records and charges.
