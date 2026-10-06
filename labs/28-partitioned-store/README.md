# Lab 28 Partitioned Store

This lab simulates placing SigRaft jobs on replicated storage nodes. Its
Python import name is `relay`; the program does not relay traffic. The
replicas and network behavior are modeled in memory, not deployed.

## Goal and activities

Observe how placement, read/write quorums and concurrent versions answer
different storage questions. You will change modeled replica sets and inspect
versions; this does not start storage nodes or move data between machines.
Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Hashing and model data structures use the standard library. Dependencies are
declared in `pyproject.toml`; no subscription or external store is needed.

Consistent hashing maps each task key to positions on a ring and chooses its
replicas from there. A virtual node is an extra ring position assigned to a
storage node, not another machine. Placement answers where a task belongs;
the read and write quorum sizes answer how many copies must participate.
Here `N` is the replica count, `R` the read count and `W` the write count.

1. Install and construct a versioned store in the REPL.
2. Run `pytest -q tests/test_lab_28_partitioned_store.py`. Compare the ring
   mapping in a second interpreter to detect dependence on randomized hashes.
3. Compare strong and weak quorum tests after one update. Trace which replicas
   were contacted rather than assuming replication has already completed.
4. Write two branches from the same observed version and inspect siblings.
   Resolve by explicitly observing both, not by selecting wall-clock order.

## Relay task contract

Labs 27 through 29 use these storage-oriented fields:

- `tenant_id`
- `task_id`
- `title`
- `status`
- `payload`
- `depends_on`
- `etag` or version metadata

This representation uses `title`, `payload` and `status`, not the earlier
`action` and `state` wire fields. Translating to the public job API is a
separate adapter concern.

This lab adds:

- stable hashing with virtual nodes
- configurable `N`, `R`, and `W`
- distribution and movement measurements
- immutable versions
- surfaced concurrent siblings instead of clock-based resolution

The tests compare the ring's answers with those from a second Python process
to check that placement does not change with the process-local hash seed.

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

After the editable install, inspect partition and replica objects:

```pycon
>>> import inspect
>>> import lab_28_partitioned_store as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> store = lab.ReplicatedTaskStore(
...     ["node-a", "node-b", "node-c"],
...     lab.QuorumConfig(replica_count=3, read_quorum=2, write_quorum=2),
... )
>>> key = lab.TaskKey("tenant-a", "task-17")
>>> written = store.write_task(lab.RelayTaskRecord(key=key, title="Inspect jobs"))
>>> store.read_task(key).versions[0].record.key == key
True
```

The returned version still identifies the submitted task. That successful
read is only the starting case. The sibling test writes different changes
from one earlier version, so neither new version has observed the other.
The store returns both rather than silently choosing one.

## Contribution and completion

The SigRaft job-orchestration web service needs explicit placement and conflict
policies. This independent package teaches them; it is not imported by Lab 39.
Distribution ratios in the tests belong to that synthetic key corpus, not a
production capacity guarantee. Finish with quorum and sibling behavior
explained, and `pybootstrap check` exit 0. Exit 1 means findings; exit 2
means a gate could not run. Exit Python to discard the modeled store.
