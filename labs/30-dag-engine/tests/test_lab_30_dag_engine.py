"""Tests for the relay DAG engine checkpoint."""

from __future__ import annotations

import pytest

from lab_30_dag_engine import (
    AttemptDisposition,
    AttemptOutcome,
    CycleDetectedError,
    DagEngine,
    NodeState,
    RelayNodeSpec,
    build_relay_workflow,
)


def test_cycle_reporting_names_nodes() -> None:
    engine = DagEngine(
        [
            RelayNodeSpec(task_id="a", title="A", depends_on=("c",)),
            RelayNodeSpec(task_id="b", title="B", depends_on=("a",)),
            RelayNodeSpec(task_id="c", title="C", depends_on=("b",)),
        ],
        max_parallel=2,
    )

    with pytest.raises(CycleDetectedError, match="a|b|c"):
        engine.validate()


def test_ready_ordering_is_deterministic_and_parallelism_is_bounded() -> None:
    engine = DagEngine(
        [
            RelayNodeSpec(task_id="discover", title="Discover"),
            RelayNodeSpec(task_id="fanout-a", title="A", depends_on=("discover",)),
            RelayNodeSpec(task_id="fanout-b", title="B", depends_on=("discover",)),
            RelayNodeSpec(task_id="fanout-c", title="C", depends_on=("discover",)),
            RelayNodeSpec(
                task_id="persist",
                title="Persist",
                depends_on=("fanout-a", "fanout-b", "fanout-c"),
            ),
        ],
        max_parallel=2,
    )

    result = engine.run(
        {
            "discover": [AttemptOutcome(AttemptDisposition.SUCCESS, duration=1)],
            "fanout-a": [AttemptOutcome(AttemptDisposition.SUCCESS, duration=2)],
            "fanout-b": [AttemptOutcome(AttemptDisposition.SUCCESS, duration=2)],
            "fanout-c": [AttemptOutcome(AttemptDisposition.SUCCESS, duration=2)],
            "persist": [AttemptOutcome(AttemptDisposition.SUCCESS, duration=1)],
        }
    )

    assert result.max_parallel_seen == 2
    assert [attempt.task_id for attempt in result.attempts] == [
        "discover",
        "fanout-a",
        "fanout-b",
        "fanout-c",
        "persist",
    ]
    assert [attempt.task_id for attempt in result.attempts if attempt.started_at == 1] == [
        "fanout-a",
        "fanout-b",
    ]
    assert result.node_results["persist"].state is NodeState.SUCCEEDED


def test_failure_blocks_descendants_but_independent_branches_continue() -> None:
    engine = DagEngine(
        [
            RelayNodeSpec(task_id="a", title="A"),
            RelayNodeSpec(task_id="b", title="B", depends_on=("a",)),
            RelayNodeSpec(task_id="c", title="C", depends_on=("b",)),
            RelayNodeSpec(task_id="x", title="X"),
            RelayNodeSpec(task_id="y", title="Y", depends_on=("x",)),
        ],
        max_parallel=2,
    )

    result = engine.run(
        {
            "b": [AttemptOutcome(AttemptDisposition.FAILURE, duration=1, detail="boom")],
        }
    )

    assert result.node_results["a"].state is NodeState.SUCCEEDED
    assert result.node_results["b"].state is NodeState.FAILED
    assert result.node_results["c"].state is NodeState.SKIPPED
    assert result.node_results["x"].state is NodeState.SUCCEEDED
    assert result.node_results["y"].state is NodeState.SUCCEEDED


def test_retryable_node_runs_again_and_then_unblocks_downstream() -> None:
    nodes = build_relay_workflow()
    engine = DagEngine(nodes, max_parallel=2)

    result = engine.run(
        {
            "run-relay-task": [
                AttemptOutcome(AttemptDisposition.RETRY, duration=1, detail="transient"),
                AttemptOutcome(AttemptDisposition.SUCCESS, duration=1),
            ]
        }
    )

    run_task_id = "run-relay-task"
    retry_attempts = [
        attempt.task_id for attempt in result.attempts if attempt.task_id == run_task_id
    ]

    assert retry_attempts == [run_task_id, run_task_id]
    assert result.node_results["run-relay-task"].attempts == 2
    assert result.node_results["publish-run-metrics"].state is NodeState.SUCCEEDED
