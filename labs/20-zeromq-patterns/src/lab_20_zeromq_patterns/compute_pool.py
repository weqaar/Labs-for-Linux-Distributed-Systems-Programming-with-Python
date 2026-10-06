"""Run relay work with Ray tasks and actors on a single-node local cluster.

Unlike Celery's broker delivery, Ray schedules work on a joined cluster and
returns an object reference. The result becomes available through that reference;
there is no durable message queue here.

The ledger actor serializes reservations for each task ID, but stores them only
in process memory. Losing the actor loses its reservations and recorded outcomes.
Ray can retry configured compute failures; this module does not recover a
submission after the submitting process dies.
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
    """Settings for local Ray startup, pending work and ledger retention.

    ``ledger_name`` scopes the idempotency ledger actor. Pools built with the
    same name on the same cluster share one ledger and therefore share
    duplicate detection and retry counts; a different name gets its own, independent
    ledger on the same cluster.

    ``allow_usage_stats=False`` disables Ray usage statistics during local
    startup, even if the parent environment enables them. ``True`` leaves the
    environment unchanged; it does not itself enable telemetry. Ray may then
    use its default collection policy if no opt-out was supplied.
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

    Set ``RAY_USAGE_STATS_ENABLED=0`` before ``ray.init()`` so its child
    processes inherit the opt-out. ``setdefault`` would leave an existing
    opt-in unchanged. Restore the previous value, or remove the variable if
    absent before entry, when the context exits. If usage statistics are
    allowed, leave the environment untouched.
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
    """Start a local Ray cluster unless this process is already connected.

    ``ray.init()`` starts real local scheduler, object-store and worker
    processes; no external head node is needed. Force usage statistics off
    during startup unless ``settings.allow_usage_stats`` is true. The caller
    that starts the cluster is responsible for stopping it.
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
    """Return the action and target used to detect conflicting task submissions."""

    return f"{submission.action.value}:{submission.target}"


class _LedgerState:
    """Track task reservations, fingerprints and outcomes in process memory.

    The Ray actor wrapper serializes calls, so only one submission can reserve
    a task ID at a time. Concurrent duplicates wait for its outcome; conflicting
    action or target values are rejected. Direct callers of this plain class
    must provide their own serialization.

    Actor loss discards this state. Retention above ``max_entries`` evicts the
    least recently touched entry that is not in flight. In-flight entries are
    never evicted, so they can temporarily exceed the configured limit.
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
        """Return the number of retained task entries, including in-flight work."""

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
    """Simulate an action outcome from the target suffix without accessing it.

    ``.part`` raises a retryable error on the first attempt; ``.missing`` always
    raises a permanent error. ``.slow`` sleeps briefly so a duplicate submission
    can overlap the reservation in a test. Other targets return immediately.
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
    """Poll for another reserved attempt's outcome using this worker's slot.

    ``poll_interval`` and ``timeout`` are seconds. If no outcome is observed
    before the deadline, raise ``RetryableComputeError``. Ray may then schedule
    another attempt, subject to the configured retry limit.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        outcome = cast("ComputeOutcome | None", ray.get(ledger.outcome.remote(task_id)))
        if outcome is not None:
            return outcome
        time.sleep(poll_interval)
    raise RetryableComputeError(f"{task_id} is still in flight in another attempt")


def _execute(ledger: ActorHandle, submission: TaskSubmission) -> ComputeOutcome:
    """Reserve a task ID and run the simulated action or reuse its outcome.

    Record ``PermanentComputeError`` as a ``failed`` outcome. Release the
    reservation on ``RetryableComputeError`` and propagate it for Ray's retry
    policy. Fingerprint conflicts and other exceptions also propagate; they
    are not converted into job outcomes.
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
    """Limit outstanding Ray tasks and share duplicate detection through an actor.

    At ``max_pending``, submission waits for a result to become ready before
    scheduling more work. Collected results remain in memory until ``drain``;
    callers should drain periodically to release them.

    An identical submission already pending in this pool reuses its object
    reference. Other submissions reach the named ledger shared by pools on the
    same cluster. Replay is possible only while that ledger retains the entry.
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
        """Return a Ray object reference, waiting for capacity before scheduling."""

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
        """Return task IDs whose object references this pool has not collected."""

        with self._lock:
            return frozenset(self._pending.values())

    def drain(self) -> tuple[ComputeOutcome, ...]:
        """Wait for every outstanding reference and return every outcome.

        Include outcomes collected while ``submit`` waited for capacity, sorted
        by task ID. Clear pending references even if collecting a result raises,
        so later calls do not repeatedly wait on the same failed reference.
        Propagate the error rather than returning a partial result tuple.
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
        """Return the number of task entries retained by the shared ledger actor."""

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
