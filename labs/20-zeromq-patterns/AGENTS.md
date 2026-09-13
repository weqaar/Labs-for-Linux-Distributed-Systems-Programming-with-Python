# Lab 20 Zeromq Patterns

Orientation for anyone, human or AI, working in this repository.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Layout

```
src/lab_20_zeromq_patterns/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Account for PUB/SUB loss with counters rather than sleeps or timing guesses.
- Real PUB/SUB tests must wait for the XPUB subscription frame, use finite
  timeouts, set zero linger, and close their context.
- DEALER/ROUTER command recovery is keyed by request ID, not by retry count.
- Keep Celery payloads JSON-only and retain late acknowledgement, worker-loss
  redelivery, prefetch one, bounded retries, queue routing and visibility
  timeout as one reviewed delivery policy.
- Use eager and memory transports for deterministic gates. A live Valkey worker
  is an optional integration exercise.
- The Ray checkpoint joins a real, single-node local cluster with no
  external head node; it is not a fake, but it is also not a live multi-node
  cluster. `ray.init()` still starts several of Ray's own processes on this
  host, a raylet, a head process and an object store. Keep it that way for
  the default gate.
- Ray's usage-stats telemetry opt-out is forced unconditionally for the
  scope of starting the local cluster, not merely defaulted with
  `setdefault`, since a parent shell that already opted in would otherwise
  leave the gate non-deterministic. The forced value is restored to whatever
  the process had before once the cluster has started, so this does not
  permanently mutate the caller's own environment.
  `RayComputeSettings(allow_usage_stats=True)` is the explicit, documented
  way to leave that variable alone instead; do not reintroduce
  `os.environ.setdefault` as a substitute for it.
- A retried Ray task runs in a fresh worker process with no memory of the
  attempt before it. Idempotency and attempt counting must live in an actor
  or other shared state, not in a module-level variable.
- Only the `RelayComputePool` that started the local cluster may stop it.
  A second pool built while one is already joined must leave it running.
- The idempotency ledger actor makes one task ID's concurrent submissions
  atomic, not durable: it decides, one call at a time, whether a submission
  should proceed, replay a recorded outcome, wait for one already running, or
  be rejected as a fingerprint conflict. Its state is process memory; a
  restarted actor keeps none of it unless that state was itself checkpointed
  to durable storage. Do not describe it as durable or durable-enough in
  code, docs or a commit message.
- A task ID resubmitted with a different action or target must raise
  `TaskFingerprintConflictError`, never be honoured under the earlier
  submission's identity and never silently overwrite it.
- The ledger's retained entries are bounded by `max_ledger_entries` and
  evicted least-recently-touched first, skipping anything still in flight.
  A change to the eviction policy needs a test that actually exceeds the
  bound and asserts on `ledger_size()`, not just a code review claim.
- `submit()` validates a requested resource label against
  `ray.cluster_resources()` before scheduling and raises
  `UnknownResourceLabelError` synchronously. Do not remove that check on the
  assumption Ray itself will reject an unknown label; it will not, it queues
  forever instead.
- `drain()` and `shutdown()` clear their own bookkeeping, and release the
  cluster they own, in a `finally` block, so a raised error, such as a
  fingerprint conflict surfacing from a drained task, still leaves cleanup
  done and the original error still propagating. Do not move that cleanup
  after the code that can raise.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
