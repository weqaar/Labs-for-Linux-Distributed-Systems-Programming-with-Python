"""Ray task and actor boundary for relay compute, on a single-node local cluster.

This is a distinct pattern from the Celery boundary in ``background_worker``.
Celery sends a JSON message to a broker and a worker process picks it up
later; the broker is a durable record only once its delivery is configured
that way, a persistent message on a durable queue with a publisher confirm
and a result backend policy that matches. Ray schedules a task or actor
directly onto a cluster it is joined to and hands back an object reference
immediately; there is no broker, and the object store, not a queue, holds
the value until something asks for it.

Two things a broker gives you for free do not come from Ray on their own, and
this module exists to be honest about both. First, an actor's state lives in
the memory of one process on this node; it is not durable storage, only a
single point every concurrent attempt for one task ID is forced through, which
is enough for correctness but not for surviving that process's own death.
Second, Ray retries the compute; it does not retry the request on your behalf
if the process that submitted it is what dies. Both points are covered where
they matter below.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal, cast

import ray
from ray.actor import ActorHandle

from lab_20_zeromq_patterns.contract import TaskAction, TaskState, TaskSubmission

_USAGE_STATS_ENV_VAR = "RAY_USAGE_STATS_ENABLED"
_DEFAULT_LEDGER_NAME = "relay-compute-idempotency-ledger"


class RetryableComputeError(RuntimeError):
    """A transient compute failure eligible for a bounded Ray-managed retry."""


class PermanentComputeError(RuntimeError):
    """A relay action failure that will not improve with another attempt."""


class ComputePoolClosedError(RuntimeError):
    """Raised when submit is called after shutdown has started."""


class TaskFingerprintConflictError(RuntimeError):
    """Raised when a task ID is resubmitted with a different action or target.

    A task ID is an idempotency key, not just a label. Two submissions that
    share one but disagree on what the action or target was cannot both be
    honoured, so this is not retried and not silently overwritten; the second
    submission is rejected.
    """


class UnknownResourceLabelError(ValueError):
    """Raised when a resource label was never declared to the joined cluster.

    Requesting an undeclared label does not fail inside Ray itself; the task
    queues forever waiting for capacity that will never appear. Checking the
    label against the cluster's own advertised resources before scheduling
    turns that silent stall into a synchronous, immediate error.
    """


@dataclass(frozen=True, slots=True)
class RayComputeSettings:
    """A single-node local Ray cluster: no external head, no live network peer.

    ``ledger_name`` scopes the idempotency ledger actor. Pools built with the
    same name on the same cluster share one ledger and therefore share
    dedupe and retry memory; a different name gets its own, independent
    ledger on the same cluster.

    ``allow_usage_stats`` defaults to false: the default offline gate forces
    Ray's own supported opt-out (Ray Project, "Usage Stats Collection")
    before starting a cluster, regardless of what the parent process's own
    environment already has that variable set to, so the gate stays
    deterministic. Setting it to true is a deliberate choice to leave that
    variable as the parent environment already has it, including Ray's own
    default of collecting anonymised usage statistics if nothing has opted
    out; it is not itself a request to enable telemetry, only to stop
    forcing it off.
    """

    num_cpus: int = 2
    resource_labels: Mapping[str, float] = field(default_factory=dict)
    max_pending: int = 4
    max_retries: int = 3
    max_ledger_entries: int = 1024
    ledger_name: str = _DEFAULT_LEDGER_NAME
    allow_usage_stats: bool = False

    def __post_init__(self) -> None:
        if self.num_cpus < 1:
            raise ValueError("num_cpus must be at least one")
        if self.max_pending < 1:
            raise ValueError("max_pending must be at least one")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.max_ledger_entries < 1:
            raise ValueError("max_ledger_entries must be at least one")
        if not self.ledger_name.strip():
            raise ValueError("ledger_name must not be empty")


@dataclass(frozen=True, slots=True)
class ComputeOutcome:
    """What the ledger actor records once a task's action has run."""

    id: str
    action: TaskAction
    state: TaskState
    attempts: int


@dataclass(frozen=True, slots=True)
class ReservationResult:
    """The ledger's single, atomic decision for one submission attempt."""

    status: Literal["reserved", "replay", "in_progress", "conflict"]
    outcome: ComputeOutcome | None = None
    attempt: int = 0


@contextmanager
def _usage_stats_override(*, allow_usage_stats: bool) -> Iterator[None]:
    """Force Ray's usage-stats opt-out for the scope of one cluster startup.

    Ray collects anonymised usage statistics over the network by default
    (Ray Project, "Usage Stats Collection"); ``RAY_USAGE_STATS_ENABLED=0`` is
    its own supported way to disable that. It has to be set before
    ``ray.init()`` starts the cluster's background processes, since those
    processes inherit this value at the moment they are spawned; a plain
    ``setdefault`` is not enough to make an offline gate deterministic, since
    it leaves a parent environment that already set this variable to ``1``
    unchanged. Forcing it here, unconditionally, unless the caller has
    deliberately allowed usage stats, is what makes the choice deterministic
    regardless of the parent process's own environment. This process's own
    environment is not permanently changed by that: whatever value, or
    absence of one, was present before this context is restored once
    ``ray.init()`` inside it has returned, so only the cluster's own startup,
    and the processes spawned from it, ever sees the forced value.
    """

    previous = os.environ.get(_USAGE_STATS_ENV_VAR)
    if not allow_usage_stats:
        os.environ[_USAGE_STATS_ENV_VAR] = "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_USAGE_STATS_ENV_VAR, None)
        else:
            os.environ[_USAGE_STATS_ENV_VAR] = previous


def start_local_cluster(settings: RayComputeSettings = RayComputeSettings()) -> None:
    """Join a single-node local Ray cluster; a no-op if one is already running.

    ``ray.init()`` with no address starts Ray's own background processes, a
    raylet, a GCS, a plasma object store, on this machine, which becomes the
    cluster's head node (Ray Project, "Starting Ray"). That is several real
    local processes, not one, and nothing here is a fake: the scheduler,
    object store and worker processes are the real Ray runtime, sized down
    for a deterministic test. Usage stats collection is forced off for the
    scope of this call unless ``settings.allow_usage_stats`` deliberately
    allows it, so joining this cluster makes no outbound telemetry request
    of that kind by default, and that choice does not depend on the parent
    process's own environment already being clean.
    """

    if ray.is_initialized():
        return
    with _usage_stats_override(allow_usage_stats=settings.allow_usage_stats):
        ray.init(
            num_cpus=settings.num_cpus,
            resources=dict(settings.resource_labels),
            include_dashboard=False,
            log_to_driver=False,
            configure_logging=False,
        )


def stop_local_cluster() -> None:
    """Leave the local cluster. Safe to call when none is running."""

    if ray.is_initialized():
        ray.shutdown()


def _fingerprint_of(submission: TaskSubmission) -> str:
    """The part of a submission that must stay fixed for one task ID."""

    return f"{submission.action.value}:{submission.target}"


class _LedgerState:
    """The single point every concurrent attempt for one task ID passes through.

    This is a plain, undecorated class, unit-testable directly in this
    process, and wrapped as a Ray actor immediately below. What it holds is
    process memory on one node, not durable storage: if the actor wrapping
    it dies without a restart, everything it knows is gone with it. What it
    provides is not durability but atomicity, Ray delivers an actor's calls
    one at a time (Ray Project, "Actors"), so ``reserve`` below is the one
    place a race between two submissions of the same task ID is actually
    decided, whichever call it serves first wins, and the loser is told to
    wait for that winner's outcome rather than run the action again. Entries
    are bounded by ``max_entries``: the oldest entry that is not currently
    in flight is forgotten once that bound is exceeded, so this actor's
    memory does not grow without limit across a long-running pool.
    """

    def __init__(self, max_entries: int) -> None:
        self._max_entries = max_entries
        self._order: OrderedDict[str, None] = OrderedDict()
        self._fingerprints: dict[str, str] = {}
        self._attempts: dict[str, int] = {}
        self._outcomes: dict[str, ComputeOutcome] = {}
        self._in_flight: set[str] = set()

    def reserve(self, task_id: str, fingerprint: str) -> ReservationResult:
        stored_fingerprint = self._fingerprints.get(task_id)
        if stored_fingerprint is not None and stored_fingerprint != fingerprint:
            return ReservationResult(status="conflict")
        outcome = self._outcomes.get(task_id)
        if outcome is not None:
            self._touch(task_id)
            return ReservationResult(status="replay", outcome=outcome)
        if task_id in self._in_flight:
            return ReservationResult(status="in_progress")
        self._in_flight.add(task_id)
        self._fingerprints[task_id] = fingerprint
        attempt = self._attempts.get(task_id, 0) + 1
        self._attempts[task_id] = attempt
        self._touch(task_id)
        return ReservationResult(status="reserved", attempt=attempt)

    def release(self, task_id: str) -> None:
        """Give up a reservation without recording an outcome.

        Used when an attempt raised the retryable error: Ray schedules the
        next attempt as a fresh task, and that attempt must be able to
        reserve the task ID again rather than see it as still in flight.
        """

        self._in_flight.discard(task_id)

    def record(self, outcome: ComputeOutcome) -> ComputeOutcome:
        self._outcomes[outcome.id] = outcome
        self._in_flight.discard(outcome.id)
        self._touch(outcome.id)
        return outcome

    def outcome(self, task_id: str) -> ComputeOutcome | None:
        return self._outcomes.get(task_id)

    def size(self) -> int:
        """Entries currently retained, for the bounded-memory test."""

        return len(self._order)

    def _touch(self, task_id: str) -> None:
        self._order.pop(task_id, None)
        self._order[task_id] = None
        self._evict()

    def _evict(self) -> None:
        while len(self._order) > self._max_entries:
            for candidate in self._order:
                if candidate not in self._in_flight:
                    self._forget(candidate)
                    break
            else:
                break  # every remaining entry is in flight; nothing evictable yet

    def _forget(self, task_id: str) -> None:
        self._order.pop(task_id, None)
        self._fingerprints.pop(task_id, None)
        self._attempts.pop(task_id, None)
        self._outcomes.pop(task_id, None)


# Wrapping the plain class here, rather than with ``@ray.remote`` on the
# class statement, keeps ``_LedgerState`` importable and directly
# instantiable for a unit test while ``_IdempotencyLedger`` stays what
# ``RelayComputePool`` schedules onto the cluster.
_IdempotencyLedger = ray.remote(num_cpus=0)(_LedgerState)


def _run_action(submission: TaskSubmission, attempt: int) -> None:
    """The relay action, with classified failures for the retry policy to see.

    A target ending in ``.part`` has not finished landing on its first
    attempt; that is a dependency that is not ready yet, not a defect in the
    request, so it raises the retryable error Ray is configured to catch. A
    target ending in ``.missing`` fails validation and will not improve with
    another attempt, the same rule the Celery section gives for input that is
    simply wrong: record it as failed rather than retrying it. A target
    ending in ``.slow`` pretends to take real wall time, which is what the
    concurrent-duplicate test uses to hold a reservation open long enough to
    prove a second, overlapping submission waits for it instead of running
    the action a second time.
    """

    if submission.target.endswith(".missing"):
        raise PermanentComputeError(f"{submission.target} does not exist")
    if submission.target.endswith(".part") and attempt < 2:
        raise RetryableComputeError(f"{submission.target} has not finished landing")
    if submission.target.endswith(".slow"):
        time.sleep(0.3)


def _await_settled_outcome(
    ledger: ActorHandle, task_id: str, *, poll_interval: float = 0.05, timeout: float = 2.0
) -> ComputeOutcome:
    """Wait out another attempt already reserved for this task ID.

    This spends this worker's slot polling rather than doing useful work, a
    real cost, not a free lock; it is correct only because ``reserve`` above
    is the atomic decision point that guarantees exactly one attempt is ever
    running the action for a given task ID at a time. If the winning attempt
    takes longer than ``timeout``, this raises the retryable error instead of
    hanging, so Ray's own retry schedules a fresh attempt that will see the
    outcome once it exists.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        outcome = cast("ComputeOutcome | None", ray.get(ledger.outcome.remote(task_id)))
        if outcome is not None:
            return outcome
        time.sleep(poll_interval)
    raise RetryableComputeError(f"{task_id} is still in flight in another attempt")


def _execute(ledger: ActorHandle, submission: TaskSubmission) -> ComputeOutcome:
    """The Ray task body: one atomic reservation, then the idempotent action.

    A permanent failure is caught here and recorded as a relay ``failed``
    outcome rather than left to propagate, so the pool's contract stays
    ``succeeded`` or ``failed`` for every relay action regardless of which
    library raised along the way. A transient failure releases the
    reservation and is left to propagate: Ray's own ``retry_exceptions`` is
    what schedules the next attempt.
    """

    fingerprint = _fingerprint_of(submission)
    reservation = cast(
        ReservationResult, ray.get(ledger.reserve.remote(submission.id, fingerprint))
    )
    if reservation.status == "conflict":
        raise TaskFingerprintConflictError(
            f"{submission.id} was already submitted with a different action or target"
        )
    if reservation.status == "replay":
        return cast(ComputeOutcome, reservation.outcome)
    if reservation.status == "in_progress":
        return _await_settled_outcome(ledger, submission.id)

    attempt = reservation.attempt
    try:
        _run_action(submission, attempt)
    except PermanentComputeError:
        outcome = ComputeOutcome(
            id=submission.id, action=submission.action, state=TaskState.FAILED, attempts=attempt
        )
        return cast(ComputeOutcome, ray.get(ledger.record.remote(outcome)))
    except RetryableComputeError:
        ray.get(ledger.release.remote(submission.id))
        raise
    outcome = ComputeOutcome(
        id=submission.id, action=submission.action, state=TaskState.SUCCEEDED, attempts=attempt
    )
    return cast(ComputeOutcome, ray.get(ledger.record.remote(outcome)))


class RelayComputePool:
    """A bounded, typed adapter over Ray tasks for one relay checkpoint.

    Submission is bounded: once ``max_pending`` results are outstanding,
    ``submit`` blocks on the oldest one rather than growing an unbounded
    queue, the same shape as the ZeroMQ high water mark and the Celery
    prefetch multiplier, applied at the scheduler instead of a socket or a
    broker. A second submission for a task ID already outstanding in this
    pool reuses that same object reference instead of scheduling a duplicate
    task; a submission for a task ID this pool is not currently tracking
    still reaches the shared ledger, which is what makes the same guarantee
    hold across two pool instances joined to one cluster, not just within
    one.
    """

    def __init__(self, settings: RayComputeSettings = RayComputeSettings()) -> None:
        self._owns_cluster = not ray.is_initialized()
        if self._owns_cluster:
            start_local_cluster(settings)
        self._settings = settings
        self._ledger = cast(
            ActorHandle,
            _IdempotencyLedger.options(name=settings.ledger_name, get_if_exists=True).remote(
                settings.max_ledger_entries
            ),
        )
        self._execute = ray.remote(_execute)
        self._pending: dict[ray.ObjectRef, str] = {}
        self._active_refs: dict[str, ray.ObjectRef] = {}
        self._active_fingerprints: dict[str, str] = {}
        self._collected: list[ComputeOutcome] = []
        self._closed = False
        self._lock = threading.Lock()

    def submit(
        self, submission: TaskSubmission, *, resource_label: str | None = None
    ) -> ray.ObjectRef:
        """Schedule one relay task, applying backpressure before its own."""

        if self._closed:
            raise ComputePoolClosedError("compute pool is shutting down")
        if resource_label is not None and resource_label not in ray.cluster_resources():
            raise UnknownResourceLabelError(
                f"resource label {resource_label!r} was never declared to this cluster"
            )
        fingerprint = _fingerprint_of(submission)
        with self._lock:
            existing_ref = self._active_refs.get(submission.id)
            if existing_ref is not None:
                if self._active_fingerprints[submission.id] != fingerprint:
                    raise TaskFingerprintConflictError(
                        f"{submission.id} is already in flight with a different action or target"
                    )
                return existing_ref
            self._drain_to_capacity_locked()
            options: dict[str, object] = {
                "max_retries": self._settings.max_retries,
                "retry_exceptions": (RetryableComputeError,),
            }
            if resource_label:
                options["resources"] = {resource_label: 1}
            ref = self._execute.options(**options).remote(self._ledger, submission)
            self._pending[ref] = submission.id
            self._active_refs[submission.id] = ref
            self._active_fingerprints[submission.id] = fingerprint
            return ref

    def pending(self) -> frozenset[str]:
        """Task IDs with an outstanding object reference, for the HWM test."""

        with self._lock:
            return frozenset(self._pending.values())

    def drain(self) -> tuple[ComputeOutcome, ...]:
        """Wait for every outstanding reference and return every outcome.

        This includes outcomes already collected by backpressure inside
        ``submit``: a result reclaimed to make room for the next submission
        is still a result, not a dropped message the way a PUB socket past
        its high water mark drops one. Bookkeeping for the refs in this
        batch is cleared in ``finally``: a task that raised, such as a
        propagated :class:`TaskFingerprintConflictError`, must not be left
        behind as a permanently pending reference that poisons every later
        call to ``drain`` or ``shutdown``. The error itself still propagates.
        """

        with self._lock:
            refs = list(self._pending)
            try:
                new_outcomes = cast("list[ComputeOutcome]", ray.get(refs)) if refs else []
            finally:
                for ref in refs:
                    task_id = self._pending.pop(ref, None)
                    if task_id is not None:
                        self._active_refs.pop(task_id, None)
                        self._active_fingerprints.pop(task_id, None)
            outcomes = self._collected + new_outcomes
            self._collected = []
            return tuple(sorted(outcomes, key=lambda outcome: outcome.id))

    def ledger_size(self) -> int:
        """Entries the shared ledger actor currently retains."""

        return cast(int, ray.get(self._ledger.size.remote()))

    def shutdown(self) -> None:
        """Drain outstanding work, then release the ledger and the cluster.

        Cleanup runs in ``finally`` so a failure surfaced by ``drain``, such
        as a propagated :class:`TaskFingerprintConflictError`, still leaves
        the cluster this pool owns stopped rather than orphaned; the
        original error is not swallowed, it propagates after cleanup runs.
        """

        if self._closed:
            return
        self._closed = True
        try:
            self.drain()
        finally:
            if self._owns_cluster:
                ray.kill(self._ledger)
                stop_local_cluster()

    def _drain_to_capacity_locked(self) -> None:
        while len(self._pending) >= self._settings.max_pending:
            done, _ = ray.wait(list(self._pending), num_returns=1)
            for ref in done:
                task_id = self._pending.pop(ref, None)
                if task_id is not None:
                    self._active_refs.pop(task_id, None)
                    self._active_fingerprints.pop(task_id, None)
                self._collected.append(cast(ComputeOutcome, ray.get(ref)))
