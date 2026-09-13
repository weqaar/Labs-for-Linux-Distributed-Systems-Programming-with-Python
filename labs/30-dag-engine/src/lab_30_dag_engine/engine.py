"""Deterministic DAG execution engine for relay workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from heapq import heappop, heappush

from lab_30_dag_engine.contract import AttemptDisposition, AttemptOutcome, NodeState, RelayNodeSpec


class CycleDetectedError(Exception):
    """Raised before execution when the graph contains a cycle."""

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        super().__init__(f"cycle detected: {' -> '.join(cycle)}")


class DuplicateNodeError(Exception):
    """Raised when the DAG contains duplicate task ids."""


class MissingDependencyError(Exception):
    """Raised when a node references an unknown upstream node."""


class InvalidParallelismError(Exception):
    """Raised when the scheduler is configured with an invalid worker limit."""


class AttemptPlanExhaustedError(Exception):
    """Raised when a test forgets to provide enough planned outcomes."""


@dataclass(frozen=True)
class AttemptRecord:
    """Lightweight immutable attempt event."""

    task_id: str
    attempt: int
    disposition: AttemptDisposition
    started_at: int
    finished_at: int


@dataclass(frozen=True)
class NodeResult:
    """Immutable node result."""

    task_id: str
    state: NodeState
    attempts: int
    last_error: str | None = None


@dataclass(frozen=True)
class RunResult:
    """Immutable DAG run summary."""

    node_results: dict[str, NodeResult]
    attempts: tuple[AttemptRecord, ...]
    max_parallel_seen: int
    total_duration: int


class DagEngine:
    """Deterministic scheduler built on graphlib.TopologicalSorter."""

    def __init__(
        self,
        nodes: tuple[RelayNodeSpec, ...] | list[RelayNodeSpec],
        *,
        max_parallel: int,
    ) -> None:
        if max_parallel < 1:
            raise InvalidParallelismError("max_parallel must be positive")
        indexed = {node.task_id: node for node in nodes}
        if len(indexed) != len(tuple(nodes)):
            raise DuplicateNodeError("task ids must be unique")
        missing = sorted(
            dependency
            for node in nodes
            for dependency in node.depends_on
            if dependency not in indexed
        )
        if missing:
            raise MissingDependencyError(", ".join(missing))
        self._nodes = indexed
        self._children = _children_map(tuple(nodes))
        self.max_parallel = max_parallel

    def validate(self) -> None:
        sorter = TopologicalSorter(_graph(self._nodes))
        try:
            sorter.prepare()
        except CycleError as exc:
            cycle = tuple(exc.args[1]) if len(exc.args) > 1 else tuple()
            raise CycleDetectedError(cycle) from exc

    def run(
        self,
        attempt_plans: Mapping[str, tuple[AttemptOutcome, ...] | list[AttemptOutcome]],
    ) -> RunResult:
        self.validate()
        sorter = TopologicalSorter(_graph(self._nodes))
        sorter.prepare()
        ready = sorted(sorter.get_ready())
        current_time = 0
        running: list[tuple[int, str, int, AttemptOutcome, int]] = []
        attempt_counts = {task_id: 0 for task_id in self._nodes}
        results = {
            task_id: NodeResult(task_id=task_id, state=NodeState.PENDING, attempts=0)
            for task_id in self._nodes
        }
        attempt_records: list[AttemptRecord] = []
        max_parallel_seen = 0

        while ready or running:
            while ready and len(running) < self.max_parallel:
                task_id = ready.pop(0)
                attempt_counts[task_id] += 1
                attempt_number = attempt_counts[task_id]
                outcome = _planned_outcome(task_id, attempt_number, attempt_plans)
                results[task_id] = NodeResult(
                    task_id=task_id,
                    state=NodeState.RUNNING,
                    attempts=attempt_number,
                )
                started_at = current_time
                finished_at = current_time + outcome.duration
                heappush(running, (finished_at, task_id, attempt_number, outcome, started_at))
                max_parallel_seen = max(max_parallel_seen, len(running))

            if not running:
                break

            current_time = running[0][0]
            completed: list[tuple[int, str, int, AttemptOutcome, int]] = []
            while running and running[0][0] == current_time:
                completed.append(heappop(running))

            for finished_at, task_id, attempt_number, outcome, started_at in sorted(
                completed,
                key=lambda item: item[1],
            ):
                attempt_records.append(
                    AttemptRecord(
                        task_id=task_id,
                        attempt=attempt_number,
                        disposition=outcome.disposition,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                )
                node = self._nodes[task_id]
                if outcome.disposition is AttemptDisposition.SUCCESS:
                    results[task_id] = NodeResult(
                        task_id=task_id,
                        state=NodeState.SUCCEEDED,
                        attempts=attempt_number,
                    )
                    sorter.done(task_id)
                    ready.extend(sorter.get_ready())
                    ready.sort()
                    continue
                if (
                    outcome.disposition is AttemptDisposition.RETRY
                    and attempt_number < node.max_attempts
                ):
                    results[task_id] = NodeResult(
                        task_id=task_id,
                        state=NodeState.PENDING,
                        attempts=attempt_number,
                        last_error=outcome.detail,
                    )
                    ready.append(task_id)
                    ready.sort()
                    continue
                results[task_id] = NodeResult(
                    task_id=task_id,
                    state=NodeState.FAILED,
                    attempts=attempt_number,
                    last_error=outcome.detail or "task failed",
                )

        failed = {
            task_id for task_id, result in results.items() if result.state is NodeState.FAILED
        }
        skipped = set()
        for task_id in failed:
            skipped.update(_descendants(task_id, self._children))
        for task_id in sorted(skipped):
            if results[task_id].state is NodeState.PENDING:
                results[task_id] = NodeResult(
                    task_id=task_id,
                    state=NodeState.SKIPPED,
                    attempts=0,
                )

        return RunResult(
            node_results=results,
            attempts=tuple(attempt_records),
            max_parallel_seen=max_parallel_seen,
            total_duration=current_time,
        )


def build_relay_workflow() -> tuple[RelayNodeSpec, ...]:
    """Return the relay DAG reused by the Airflow checkpoint."""
    return (
        RelayNodeSpec(
            task_id="discover-pending-tasks",
            title="Discover pending relay tasks",
        ),
        RelayNodeSpec(
            task_id="hydrate-task-context",
            title="Hydrate relay task context",
            depends_on=("discover-pending-tasks",),
        ),
        RelayNodeSpec(
            task_id="run-relay-task",
            title="Run relay task handler",
            depends_on=("hydrate-task-context",),
            max_attempts=2,
        ),
        RelayNodeSpec(
            task_id="persist-task-status",
            title="Persist relay task status",
            depends_on=("run-relay-task",),
        ),
        RelayNodeSpec(
            task_id="publish-run-metrics",
            title="Publish relay run metrics",
            depends_on=("persist-task-status",),
        ),
    )


def _planned_outcome(
    task_id: str,
    attempt_number: int,
    attempt_plans: Mapping[
        str,
        tuple[AttemptOutcome, ...] | list[AttemptOutcome],
    ],
) -> AttemptOutcome:
    plan = attempt_plans.get(task_id)
    if plan is None:
        return AttemptOutcome(disposition=AttemptDisposition.SUCCESS, duration=1)
    if attempt_number - 1 >= len(plan):
        raise AttemptPlanExhaustedError(task_id)
    return plan[attempt_number - 1]


def _graph(nodes: Mapping[str, RelayNodeSpec]) -> dict[str, tuple[str, ...]]:
    return {task_id: node.depends_on for task_id, node in nodes.items()}


def _children_map(nodes: tuple[RelayNodeSpec, ...]) -> dict[str, set[str]]:
    children = {node.task_id: set() for node in nodes}
    for node in nodes:
        for dependency in node.depends_on:
            children[dependency].add(node.task_id)
    return children


def _descendants(task_id: str, children: dict[str, set[str]]) -> set[str]:
    stack = list(children[task_id])
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children[current])
    return seen
