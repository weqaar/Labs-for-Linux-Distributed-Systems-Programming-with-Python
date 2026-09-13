"""Tests for the offline Ray compute pool checkpoint.

These tests join a real, single-node local Ray cluster: no external head, no
live network peer, started once for this module and torn down at the end.
Every task ID that must not collide with another test is unique per test; the
idempotency ledger is shared across the whole module by default, and a
repeated ID elsewhere in the file would return a cached outcome instead of a
fresh one.

Fixture-consuming tests are placed before any test that builds its own
``RelayComputePool``. A pool built while no cluster is joined becomes that
cluster's owner and its ``shutdown`` stops the cluster outright, so every
standalone pool below still depends on the ``compute_pool`` fixture, even
when it does not use the fixture value, purely to guarantee a cluster is
already joined before it is constructed.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import ray

from lab_20_zeromq_patterns import (
    ComputeOutcome,
    ComputePoolClosedError,
    RayComputeSettings,
    RelayComputePool,
    TaskAction,
    TaskFingerprintConflictError,
    TaskState,
    TaskSubmission,
    UnknownResourceLabelError,
    start_local_cluster,
)
from lab_20_zeromq_patterns.compute_pool import (
    RetryableComputeError,
    _await_settled_outcome,
    _execute,
    _LedgerState,
)


def make_submission(task_id: str, target: str = "blob://relay/inbox/17") -> TaskSubmission:
    return TaskSubmission(
        id=task_id,
        action=TaskAction.INDEX,
        target=target,
        submitted_at=datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc),
    )


@pytest.fixture(scope="module")
def compute_pool():
    settings = RayComputeSettings(
        num_cpus=2, resource_labels={"relay-io": 1}, max_pending=2, max_retries=2
    )
    pool = RelayComputePool(settings)
    yield pool
    pool.shutdown()


def test_settings_reject_a_non_positive_cpu_count() -> None:
    with pytest.raises(ValueError, match="num_cpus"):
        RayComputeSettings(num_cpus=0)


def test_settings_reject_a_non_positive_pending_limit() -> None:
    with pytest.raises(ValueError, match="max_pending"):
        RayComputeSettings(max_pending=0)


def test_settings_reject_a_negative_retry_count() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        RayComputeSettings(max_retries=-1)


def test_settings_reject_a_non_positive_ledger_capacity() -> None:
    with pytest.raises(ValueError, match="max_ledger_entries"):
        RayComputeSettings(max_ledger_entries=0)


def test_settings_reject_an_empty_ledger_name() -> None:
    with pytest.raises(ValueError, match="ledger_name"):
        RayComputeSettings(ledger_name="  ")


def test_settings_default_to_forcing_the_usage_stats_opt_out() -> None:
    assert RayComputeSettings().allow_usage_stats is False


def test_start_local_cluster_forces_usage_stats_opt_out_by_default() -> None:
    """The default gate must be deterministic regardless of a dirty parent env.

    A plain ``os.environ.setdefault`` would leave an already-set variable
    alone; forcing it unconditionally while ``ray.init()`` runs, then
    restoring whatever was there before, is what makes the offline gate's
    telemetry posture the same on every run, not only on a clean shell.
    ``ray.init`` is patched so this exercises the real env-handling logic
    without starting a second real cluster alongside the module fixture's.
    """

    os.environ.pop("RAY_USAGE_STATS_ENABLED", None)
    seen: dict[str, str | None] = {}

    def fake_init(**_: object) -> None:
        seen["during"] = os.environ.get("RAY_USAGE_STATS_ENABLED")

    with (
        patch("lab_20_zeromq_patterns.compute_pool.ray.is_initialized", return_value=False),
        patch("lab_20_zeromq_patterns.compute_pool.ray.init", side_effect=fake_init) as mock_init,
    ):
        start_local_cluster(RayComputeSettings())

    mock_init.assert_called_once()
    assert seen["during"] == "0"
    assert "RAY_USAGE_STATS_ENABLED" not in os.environ


def test_start_local_cluster_restores_a_prior_explicit_opt_in() -> None:
    """Forcing the opt-out during startup must not overwrite it afterward."""

    os.environ["RAY_USAGE_STATS_ENABLED"] = "1"
    seen: dict[str, str | None] = {}

    def fake_init(**_: object) -> None:
        seen["during"] = os.environ.get("RAY_USAGE_STATS_ENABLED")

    try:
        with (
            patch("lab_20_zeromq_patterns.compute_pool.ray.is_initialized", return_value=False),
            patch(
                "lab_20_zeromq_patterns.compute_pool.ray.init", side_effect=fake_init
            ) as mock_init,
        ):
            start_local_cluster(RayComputeSettings())

        mock_init.assert_called_once()
        assert seen["during"] == "0"
        assert os.environ["RAY_USAGE_STATS_ENABLED"] == "1"
    finally:
        os.environ.pop("RAY_USAGE_STATS_ENABLED", None)


def test_start_local_cluster_leaves_a_deliberate_opt_in_untouched() -> None:
    """``allow_usage_stats=True`` must not start a second real cluster either."""

    os.environ.pop("RAY_USAGE_STATS_ENABLED", None)
    seen: dict[str, str | None] = {}

    def fake_init(**_: object) -> None:
        seen["during"] = os.environ.get("RAY_USAGE_STATS_ENABLED")

    with (
        patch("lab_20_zeromq_patterns.compute_pool.ray.is_initialized", return_value=False),
        patch("lab_20_zeromq_patterns.compute_pool.ray.init", side_effect=fake_init) as mock_init,
    ):
        start_local_cluster(RayComputeSettings(allow_usage_stats=True))

    mock_init.assert_called_once()
    assert seen["during"] is None
    assert "RAY_USAGE_STATS_ENABLED" not in os.environ


def test_submit_returns_a_real_object_reference(compute_pool: RelayComputePool) -> None:
    compute_pool.drain()

    ref = compute_pool.submit(make_submission("task-101"))

    assert isinstance(ref, ray.ObjectRef)
    assert ray.get(ref) == ComputeOutcome(
        id="task-101", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, attempts=1
    )


def test_bounded_submission_never_exceeds_max_pending(compute_pool: RelayComputePool) -> None:
    compute_pool.drain()

    for suffix in range(102, 107):
        compute_pool.submit(make_submission(f"task-{suffix}"))
        assert len(compute_pool.pending()) <= 2

    outcomes = compute_pool.drain()
    assert [outcome.id for outcome in outcomes] == [f"task-{n}" for n in range(102, 107)]
    assert not compute_pool.pending()


def test_transient_failure_is_retried_and_recorded_as_two_attempts(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()

    ref = compute_pool.submit(make_submission("task-110", target="blob://relay/inbox/17.part"))

    outcome = ray.get(ref)
    assert outcome.state is TaskState.SUCCEEDED
    assert outcome.attempts == 2


def test_permanent_failure_is_recorded_failed_without_a_retry(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()

    ref = compute_pool.submit(make_submission("task-120", target="blob://relay/inbox/17.missing"))

    outcome = ray.get(ref)
    assert outcome.state is TaskState.FAILED
    assert outcome.attempts == 1


def test_idempotent_replay_reuses_the_recorded_outcome(compute_pool: RelayComputePool) -> None:
    compute_pool.drain()

    first = ray.get(compute_pool.submit(make_submission("task-130")))
    second = ray.get(compute_pool.submit(make_submission("task-130")))

    assert first == second
    assert second.attempts == 1


def test_resource_label_schedules_onto_the_declared_worker(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()

    ref = compute_pool.submit(make_submission("task-140"), resource_label="relay-io")

    assert ray.get(ref).state is TaskState.SUCCEEDED


def test_unknown_resource_label_is_rejected_synchronously(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()

    with pytest.raises(UnknownResourceLabelError):
        compute_pool.submit(make_submission("task-141"), resource_label="relay-nonexistent")

    assert not compute_pool.pending()


def test_duplicate_submission_on_one_pool_reuses_the_in_flight_reference(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()
    submission = make_submission("task-150", target="blob://relay/inbox/17.slow")

    first_ref = compute_pool.submit(submission)
    second_ref = compute_pool.submit(submission)

    assert first_ref == second_ref
    assert ray.get(first_ref).attempts == 1
    compute_pool.drain()


def test_duplicate_submission_with_a_different_fingerprint_is_rejected_while_pending(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()
    original = make_submission("task-151", target="blob://relay/inbox/17.slow")
    conflicting = make_submission("task-151", target="blob://relay/inbox/other")

    compute_pool.submit(original)
    with pytest.raises(TaskFingerprintConflictError):
        compute_pool.submit(conflicting)

    compute_pool.drain()


def test_settled_task_id_reused_with_a_different_fingerprint_is_rejected(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()
    first_ref = compute_pool.submit(make_submission("task-152"))
    assert ray.get(first_ref).state is TaskState.SUCCEEDED
    compute_pool.drain()  # clears this pool's local bookkeeping for task-152

    conflicting = make_submission("task-152", target="blob://relay/inbox/other")
    compute_pool.submit(conflicting)

    # The ledger, not this pool's own memory, is what still remembers
    # task-152's original fingerprint and rejects the mismatch. ``drain``
    # also proves the rejected reference does not linger as pending state.
    with pytest.raises(TaskFingerprintConflictError):
        compute_pool.drain()
    assert not compute_pool.pending()


def test_concurrent_duplicate_submission_across_two_pools_settles_to_one_outcome(
    compute_pool: RelayComputePool,
) -> None:
    compute_pool.drain()
    second_pool = RelayComputePool()  # attaches to the same cluster and ledger by name
    submission = make_submission("task-160", target="blob://relay/inbox/17.slow")

    # Both refs are dispatched without waiting on either: the ledger actor,
    # not either driver, is what decides which one actually runs the action.
    ref_a = compute_pool.submit(submission)
    ref_b = second_pool.submit(submission)

    outcome_a = ray.get(ref_a)
    outcome_b = ray.get(ref_b)
    assert outcome_a == outcome_b
    assert outcome_a.attempts == 1


def test_ledger_state_reserve_replay_conflict_and_eviction_run_directly() -> None:
    """Exercise ``_LedgerState`` in this process, not inside a Ray actor.

    Everything a Ray actor runs happens in its own worker process, invisible
    to coverage measurement in the driver. ``_LedgerState`` is deliberately a
    plain, undecorated class for exactly this reason: it can be instantiated
    and driven directly here, in-process, to prove out its reservation,
    replay, conflict and bounded-eviction rules without any Ray runtime at
    all, while ``_IdempotencyLedger`` wraps the same class for the cluster.
    """

    ledger = _LedgerState(max_entries=2)

    reserved = ledger.reserve("task-1", "index:target-a")
    assert reserved.status == "reserved"
    assert reserved.attempt == 1

    still_running = ledger.reserve("task-1", "index:target-a")
    assert still_running.status == "in_progress"

    conflicting = ledger.reserve("task-1", "index:target-b")
    assert conflicting.status == "conflict"

    outcome = ComputeOutcome(
        id="task-1", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, attempts=1
    )
    assert ledger.record(outcome) == outcome
    assert ledger.outcome("task-1") == outcome

    replay = ledger.reserve("task-1", "index:target-a")
    assert replay.status == "replay"
    assert replay.outcome == outcome

    # A released reservation lets a retried attempt reserve again, with the
    # attempt counter carried forward rather than restarted.
    ledger.reserve("task-2", "index:target-c")
    ledger.release("task-2")
    retried = ledger.reserve("task-2", "index:target-c")
    assert retried.status == "reserved"
    assert retried.attempt == 2
    ledger.record(
        ComputeOutcome(id="task-2", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, attempts=2)
    )

    # Exceeding max_entries forgets the oldest settled entry (task-1), so a
    # later resubmission under a different fingerprint is accepted rather
    # than rejected: eviction, not luck, is what allows it.
    ledger.reserve("task-3", "index:target-d")
    ledger.record(
        ComputeOutcome(id="task-3", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, attempts=1)
    )
    assert ledger.size() <= 2
    assert ledger.reserve("task-1", "index:target-different").status == "reserved"


def test_ledger_state_does_not_evict_entries_that_are_still_in_flight() -> None:
    """A bound is a target, not a guarantee: nothing evictable is forced out.

    Two reservations that are both still in flight can together exceed
    ``max_entries``; forgetting either one would let a second concurrent
    attempt for the same task ID slip past the reservation it depends on,
    so eviction leaves both in place until at least one of them settles.
    """

    ledger = _LedgerState(max_entries=1)

    ledger.reserve("task-a", "index:target-a")
    ledger.reserve("task-b", "index:target-b")

    assert ledger.size() == 2


def test_execute_called_directly_waits_when_already_in_progress(
    compute_pool: RelayComputePool,
) -> None:
    """Drive ``_execute`` itself into its ``in_progress`` branch directly.

    The cross-pool concurrency test above proves this branch matters, but
    the losing attempt runs inside a dispatched Ray task there, invisible to
    coverage. Reserving the task ID first, without recording an outcome,
    and then calling ``_execute`` directly for the same ID makes this
    branch run, and be measured, in this process; a background thread
    records the winning outcome shortly after so the poll inside it
    actually returns instead of timing out.
    """

    ledger = compute_pool._ledger
    fingerprint = "index:blob://relay/inbox/17"
    ray.get(ledger.reserve.remote("task-504", fingerprint))
    outcome = ComputeOutcome(
        id="task-504", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, attempts=1
    )

    def record_shortly() -> None:
        time.sleep(0.1)
        ray.get(ledger.record.remote(outcome))

    recorder = threading.Thread(target=record_shortly)
    recorder.start()
    try:
        assert _execute(ledger, make_submission("task-504")) == outcome
    finally:
        recorder.join()


def test_execute_called_directly_runs_every_action_branch_in_this_process(
    compute_pool: RelayComputePool,
) -> None:
    """Call ``_execute`` as a plain function, not through ``.remote()``.

    ``.remote()`` dispatches to a fresh worker process; calling the function
    itself runs it, and everything it calls, synchronously in this process,
    so coverage can see the reservation, retry-release, permanent-failure,
    replay and conflict branches directly instead of only through a
    dispatched task whose process coverage cannot observe.
    """

    ledger = compute_pool._ledger

    first = _execute(ledger, make_submission("task-500"))
    assert first.state is TaskState.SUCCEEDED
    assert first.attempts == 1
    assert _execute(ledger, make_submission("task-500")) == first

    with pytest.raises(TaskFingerprintConflictError):
        _execute(ledger, make_submission("task-500", target="blob://relay/inbox/other"))

    transient = make_submission("task-501", target="blob://relay/inbox/17.part")
    with pytest.raises(RetryableComputeError):
        _execute(ledger, transient)
    retried = _execute(ledger, transient)
    assert retried.state is TaskState.SUCCEEDED
    assert retried.attempts == 2

    permanent = make_submission("task-502", target="blob://relay/inbox/17.missing")
    failed = _execute(ledger, permanent)
    assert failed.state is TaskState.FAILED

    slow = make_submission("task-503", target="blob://relay/inbox/17.slow")
    assert _execute(ledger, slow).state is TaskState.SUCCEEDED


def test_await_settled_outcome_returns_once_the_winner_records_it(
    compute_pool: RelayComputePool,
) -> None:
    ledger = compute_pool._ledger
    ray.get(ledger.reserve.remote("task-510", "index:blob://relay/inbox/17"))
    outcome = ComputeOutcome(
        id="task-510", action=TaskAction.INDEX, state=TaskState.SUCCEEDED, attempts=1
    )
    ray.get(ledger.record.remote(outcome))

    assert _await_settled_outcome(ledger, "task-510", poll_interval=0.01, timeout=1.0) == outcome


def test_await_settled_outcome_times_out_when_nobody_finishes(
    compute_pool: RelayComputePool,
) -> None:
    ledger = compute_pool._ledger
    ray.get(ledger.reserve.remote("task-511", "index:blob://relay/inbox/17"))

    with pytest.raises(RetryableComputeError, match="still in flight"):
        _await_settled_outcome(ledger, "task-511", poll_interval=0.01, timeout=0.05)

    # Release the stale reservation so it does not linger for a later test
    # sharing this ledger.
    ray.get(ledger.release.remote("task-511"))


def test_shutdown_drains_pending_work_then_rejects_new_submissions(
    compute_pool: RelayComputePool,
) -> None:
    settings = RayComputeSettings(num_cpus=1, max_pending=2, max_retries=1)
    pool = RelayComputePool(settings)
    pool.submit(make_submission("task-153"))

    pool.shutdown()
    pool.shutdown()  # idempotent: a second call must not re-drain or re-kill

    with pytest.raises(ComputePoolClosedError):
        pool.submit(make_submission("task-154"))


def test_shutdown_cleans_up_the_owned_cluster_even_when_drain_raises(
    compute_pool: RelayComputePool,
) -> None:
    pool = RelayComputePool()
    pool._owns_cluster = True  # exercise the owning-pool cleanup path in isolation

    with (
        patch.object(pool, "drain", side_effect=RuntimeError("boom")),
        patch("lab_20_zeromq_patterns.compute_pool.ray.kill") as mock_kill,
        patch("lab_20_zeromq_patterns.compute_pool.stop_local_cluster") as mock_stop,
    ):
        with pytest.raises(RuntimeError, match="boom"):
            pool.shutdown()

    mock_kill.assert_called_once_with(pool._ledger)
    mock_stop.assert_called_once()


def test_ledger_state_is_bounded_by_a_tested_capacity_policy(
    compute_pool: RelayComputePool,
) -> None:
    settings = RayComputeSettings(
        num_cpus=1,
        max_pending=1,
        max_retries=0,
        max_ledger_entries=2,
        ledger_name="capacity-test-ledger",
    )
    pool = RelayComputePool(settings)
    try:
        for suffix in range(1, 4):
            ray.get(pool.submit(make_submission(f"task-{900 + suffix}")))
        assert pool.ledger_size() <= 2

        # task-901 was the oldest entry and is no longer retained, so a
        # resubmission under a different fingerprint is accepted rather than
        # rejected as a conflict: the eviction, not luck, is what allows it.
        replacement = make_submission("task-901", target="blob://relay/inbox/replacement")
        outcome = ray.get(pool.submit(replacement))
        assert outcome.state is TaskState.SUCCEEDED
    finally:
        pool.shutdown()


def test_start_local_cluster_is_a_no_op_once_joined(compute_pool: RelayComputePool) -> None:
    assert ray.is_initialized()
    start_local_cluster()  # must not attempt a second, conflicting ray.init
