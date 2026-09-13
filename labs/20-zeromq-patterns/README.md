# Lab 20 Zeromq Patterns

This checkpoint implements relay messaging at three different boundaries:

- PUSH/PULL work distribution with drain on shutdown
- PUB/SUB status fan-out with high-water drop accounting
- DEALER/ROUTER commands with recoverable request IDs
- a real XPUB/SUB socket round trip with subscription acknowledgement
- Celery background tasks with a Valkey-compatible Redis broker and backend
- a Ray compute pool of relay tasks and actors, joined to an offline local
  cluster

The model tests stay deterministic. The socket test uses `inproc` and waits for
the XPUB subscription frame rather than sleeping. Celery runs in eager mode
during the gate while retaining production settings for JSON-only messages,
late acknowledgement, worker-loss redelivery, prefetch one, routing and a
visibility timeout. The Ray tests join a real, single-node local cluster,
Ray's own raylet, head process and object store, started once for the test
module and torn down at the end; no external head node and no live network
peer are required. Ray's usage-stats telemetry is forced off for the scope of
that startup, regardless of what the parent shell's own environment already
has that variable set to, and restored to whatever it was before once the
cluster has started; `RayComputeSettings(allow_usage_stats=True)` is the
explicit, documented way to leave that variable untouched instead.

## Run a local worker

Start Valkey from the repository root, then run the worker from this lab:

```bash
make local-up
celery --app lab_20_zeromq_patterns.background_worker worker \
  --queues relay.tasks --loglevel INFO
```

The package factory is `create_relay_celery()`. Production code should create
one application at startup and inject it into the web adapter. The gate does not
start a daemon or claim that an in-memory broker proves Redis recovery.

## Run the Ray compute pool

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
ref = pool.submit(submission, resource_label='relay-io')
print(ray.get(ref))
pool.shutdown()
"
```

`RelayComputePool` starts its own local cluster if one is not already joined,
and only that cluster's owner tears it down. Production code that wants to
join an existing Ray cluster instead should call `start_local_cluster()` (or
`ray.init(address=...)` directly) before constructing the pool.

Submitting the same task ID twice, whether from one pool instance or two
sharing a cluster, resolves to one in-flight effect: the pool's idempotency
ledger reserves a task ID atomically, replays a finished outcome instead of
running the action again, and rejects a task ID resubmitted with a different
action or target as `TaskFingerprintConflictError`. That ledger's memory is
bounded; its oldest settled entry is forgotten once `max_ledger_entries` is
exceeded, never one still in flight. A resource label `submit()` has never
seen declared on the cluster raises `UnknownResourceLabelError` immediately,
rather than letting the task queue forever. `shutdown()` releases the
cluster it owns even if draining outstanding work raises.

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
```

Create an eager application with memory transports, inspect
`task_delivery_policy(app)`, and only then run the live Valkey exercise. Do
the same for Ray: build a `RelayComputePool` with a small `max_pending`,
inspect `pool.pending()` between submissions, and only then point
`start_local_cluster` at a real multi-node address.
