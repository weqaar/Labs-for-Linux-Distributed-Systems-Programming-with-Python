"""Relay task plan combining ordered and graph indexes."""

from __future__ import annotations

from dataclasses import dataclass

from .graph import DirectedGraph
from .red_black import RedBlackTree


@dataclass(frozen=True, slots=True)
class TaskSpec:
    id: str
    action: str
    priority: int

    def __post_init__(self) -> None:
        if not self.id.startswith("task-") or not self.id[5:].isdigit():
            raise ValueError("id must match task-<integer>")
        if not self.action.strip():
            raise ValueError("action must not be empty")


class RelayPlan:
    """Task dependency graph plus a stable priority index."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskSpec] = {}
        self._dependencies: DirectedGraph[str] = DirectedGraph()
        self._priorities: RedBlackTree[tuple[int, str], TaskSpec] = RedBlackTree()

    def add(self, task: TaskSpec, *, after: tuple[str, ...] = ()) -> None:
        if task.id in self._tasks:
            raise ValueError(f"task already exists: {task.id}")
        missing = tuple(dependency for dependency in after if dependency not in self._tasks)
        if missing:
            raise LookupError(f"unknown dependencies: {missing}")
        self._tasks[task.id] = task
        self._dependencies.add_node(task.id)
        for dependency in after:
            self._dependencies.add_edge(dependency, task.id)
        self._priorities.put((-task.priority, task.id), task)
        self._dependencies.topological_order()

    def execution_order(self) -> tuple[str, ...]:
        return self._dependencies.topological_order()

    def priority_order(self) -> tuple[str, ...]:
        self._priorities.validate()
        return tuple(task.id for _, task in self._priorities.items())
