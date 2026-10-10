"""Functional tests for the DAG engine checkpoint.

These tests drive the composed workflow the way an operator would run it:
``build_relay_workflow()`` supplies the job's steps and ``DagEngine.run``
executes them against planned attempt outcomes. Each test checks one whole
run, from validation through the final state of every step, using the
engine's simulated integer clock instead of real handlers or sleeps.
"""

from __future__ import annotations

import pytest

from lab_30_dag_engine import (
    AttemptDisposition,
    AttemptOutcome,
    AttemptPlanExhaustedError,
    CycleDetectedError,
    DagEngine,
    NodeState,
    RelayNodeSpec,
    build_relay_workflow,
)

STEP_ORDER = [
    "discover-pending-tasks",
    "hydrate-task-context",
    "run-relay-task",
    "persist-task-status",
    "publish-run-metrics",
]


def test_relay_workflow_runs_every_step_to_succeeded_in_dependency_order() -> None:
    engine = DagEngine(build_relay_workflow(), max_parallel=2)

    result = engine.run({})

    assert [attempt.task_id for attempt in result.attempts] == STEP_ORDER
    assert {step: item.state for step, item in result.node_results.items()} == dict.fromkeys(
        STEP_ORDER, NodeState.SUCCEEDED
    )
    assert result.total_duration == len(STEP_ORDER)
    assert all(
        earlier.finished_at <= later.started_at
        for earlier, later in zip(result.attempts, result.attempts[1:])
    )


def test_exhausted_retries_fail_the_handler_and_skip_downstream_steps() -> None:
    engine = DagEngine(build_relay_workflow(), max_parallel=2)

    result = engine.run(
        {
            "run-relay-task": [
                AttemptOutcome(AttemptDisposition.RETRY, detail="worker unavailable"),
                AttemptOutcome(AttemptDisposition.RETRY, detail="worker unavailable"),
            ]
        }
    )

    states = {step: item.state for step, item in result.node_results.items()}
    assert states == {
        "discover-pending-tasks": NodeState.SUCCEEDED,
        "hydrate-task-context": NodeState.SUCCEEDED,
        "run-relay-task": NodeState.FAILED,
        "persist-task-status": NodeState.SKIPPED,
        "publish-run-metrics": NodeState.SKIPPED,
    }
    handler = result.node_results["run-relay-task"]
    assert (handler.attempts, handler.last_error) == (2, "worker unavailable")
    assert [attempt.task_id for attempt in result.attempts].count("discover-pending-tasks") == 1


def test_workflow_with_a_back_edge_is_rejected_before_any_attempt() -> None:
    steps = list(build_relay_workflow())
    steps[0] = RelayNodeSpec(
        task_id="discover-pending-tasks",
        title="Discover pending relay tasks",
        depends_on=("publish-run-metrics",),
    )
    engine = DagEngine(steps, max_parallel=2)

    with pytest.raises(CycleDetectedError) as caught:
        engine.run({})

    assert set(caught.value.cycle) == set(STEP_ORDER)


def test_retry_without_a_planned_next_attempt_stops_the_run() -> None:
    engine = DagEngine(build_relay_workflow(), max_parallel=1)

    with pytest.raises(AttemptPlanExhaustedError, match="run-relay-task"):
        engine.run({"run-relay-task": [AttemptOutcome(AttemptDisposition.RETRY)]})
