# Lab 20 Zeromq Patterns

This lab compares three ways to move or execute SigRaft work. The Python
import name is `relay`. That name does not mean the program relays traffic.
ZeroMQ provides messaging patterns, Celery delivers background tasks through
a broker, and Ray runs tasks in a local compute cluster. The exercises include:

- PUSH/PULL work distribution with drain on shutdown
- PUB/SUB status fan-out with high-water drop accounting
- DEALER/ROUTER commands with recoverable request IDs
- a real XPUB/SUB socket round trip with subscription acknowledgement
- Celery background tasks with a Valkey-compatible Redis broker and backend
- a Ray compute pool of SigRaft jobs and actors, joined to an offline local
  cluster

The model tests use controlled inputs. The socket test uses `inproc` and waits
for the XPUB subscription frame rather than sleeping. Celery runs in eager mode
during the gate while retaining production settings for JSON-only messages,
late acknowledgement, worker-loss redelivery, prefetch one, routing and a
visibility timeout. The Ray tests start a single-node local cluster with
Ray's raylet, head process and object store. They stop it after the test
module; they do not join an external cluster. During startup, the tests turn
off Ray usage statistics even if the parent shell enabled them, then restore
the original setting. Set `RayComputeSettings(allow_usage_stats=True)` to
leave that setting untouched.

## Goal and preparation

Compare what each mechanism drops, retries and retains, and who shuts it down.
Run the in-memory models, in-process ZeroMQ socket tests, eager Celery tests
and single-node Ray tests. These are distinct experiments, not a multi-node
job deployment.

Use Python 3.10 or later with a compatible Ray wheel, and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Install from this directory using the setup below before running workers.
The declared runtime packages are pyzmq, Celery with Redis support and Ray.
The gate needs local process/IPC resources but no Azure subscription or
external cluster; the Valkey worker is optional.

1. Inspect `patterns.py`, then run
   `pytest -q tests/test_lab_20_zeromq_patterns.py`.
   Compare lost PUB/SUB messages with request-ID replay.
2. Inspect eager delivery policy in the REPL before contacting a broker.
3. Run `pytest -q tests/test_compute_pool.py` separately to observe real
   Ray workers, bounded submissions, conflicts and cleanup.
4. Only after these checks, try the optional live worker or pool below.
   Record whether each test used a model, a socket, a broker or a Ray worker.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Python REPL debugging session

Inspect delivery policy before starting sockets or a worker:

```pycon
>>> import inspect
>>> import lab_20_zeromq_patterns as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.signature(lab.create_relay_celery)
>>> help(lab.CelerySettings)
>>> help(lab.RelayComputePool)
>>> app = lab.create_relay_celery(
...     lab.CelerySettings(broker_url="memory://", result_backend="cache+memory://"),
...     eager=True,
... )
>>> lab.task_delivery_policy(app)["prefetch_multiplier"]
1
>>> app.close()
```

Eager mode executes a Celery task in the calling process rather than through
a worker. The policy value of one limits worker prefetch in the live path;
reading that setting does not demonstrate broker delivery. Compare it with
the optional Valkey exercise only after the local tests pass.

For Ray, build a `RelayComputePool` with a small `max_pending`,
inspect `pool.pending()` between submissions, and only then consider a
separately configured multi-node connection.

## Run a local worker

Start Valkey with `make local-up` from the repository root. Then return to
this lab's activated environment. The default `valkey` hostname is intended
for the Compose network; a host-shell worker must use the published loopback
port from `local/compose.yaml`.

```bash
python - <<'PY'
from lab_20_zeromq_patterns import CelerySettings, create_relay_celery
app = create_relay_celery(CelerySettings(
    broker_url="redis://127.0.0.1:6379/0",
    result_backend="redis://127.0.0.1:6379/1",
))
app.worker_main(["worker", "--queues", "relay.tasks", "--loglevel", "INFO"])
PY
```

The package factory is `create_relay_celery()`. Production code should create
one application at startup and inject it into the web adapter. The Celery tests
use eager mode; they do not start a broker worker or test Redis recovery.

## Run the Ray compute pool

The Celery example waits for broker-delivered work. This Ray example submits
one work item directly, receives a reference to its future result and waits
for that result with `ray.get`. The declared resource label limits where Ray
may schedule the item:

```bash
python -c "
from lab_20_zeromq_patterns import RayComputeSettings, RelayComputePool
from lab_20_zeromq_patterns.contract import TaskAction, TaskSubmission
from datetime import datetime, timezone
import ray

pool = RelayComputePool(RayComputeSettings(num_cpus=2, resource_labels={'relay-io': 1}))
submission = TaskSubmission(
    id='task-17', action=TaskAction.INDEX, target='blob://relay/inbox/17',
    submitted_at=datetime.now(timezone.utc),
)
try:
    ref = pool.submit(submission, resource_label='relay-io')
    print(ray.get(ref))
finally:
    pool.shutdown()
"
```

`RelayComputePool` starts its own local cluster if one is not already joined,
and only that cluster's owner tears it down. Production code that wants to
join an existing Ray cluster instead must establish that connection separately
with an appropriate `ray.init(address=...)` configuration before constructing
the pool. `start_local_cluster()` starts a local cluster, not a remote one.

Submitting the same task ID twice, whether from one pool instance or two
sharing a cluster, resolves to one in-flight effect: the pool's idempotency
ledger reserves a task ID atomically, replays a finished outcome instead of
running the action again, and rejects a task ID resubmitted with a different
action or target as `TaskFingerprintConflictError`. When retention exceeds
`max_ledger_entries`, the ledger forgets the least recently touched entry
that is not in flight. In-flight entries are never evicted, so they can
temporarily exceed that limit. A resource label `submit()` has never
seen declared on the cluster raises `UnknownResourceLabelError` immediately,
rather than letting the task queue forever. `shutdown()` releases the
cluster it owns even if draining outstanding work raises.

The ledger is process memory, not durable storage. Actor restart or eviction
can remove recorded outcomes and allow a later submission to run again.
The example actions demonstrate execution policy;
they are not implementations of a real blob indexing service.

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
## Contribution and completion

These independent experiments inform delivery and execution choices for the
SigRaft job-orchestration web service. Lab 39 lists these capabilities in its
release records but does not start a Celery worker or Ray cluster.
Finish when you can distinguish at-least-once delivery from in-memory
deduplication and show queue limits and cleanup. `pybootstrap check` must
exit 0; exit 1 means findings and exit 2 means a gate could not run.
Stop the Celery worker with Ctrl-C, shut down the Ray pool that owns its
cluster, and use `make local-down` from the root if you started the local
stack. Do not stop another user's cluster.
