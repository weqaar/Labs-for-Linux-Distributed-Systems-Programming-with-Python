"""Directed graph algorithms for relay dependencies and routes."""

from __future__ import annotations

from collections import deque
from collections.abc import Hashable, Iterable
from graphlib import CycleError as StandardCycleError
from graphlib import TopologicalSorter
from typing import Generic, TypeVar

T = TypeVar("T", bound=Hashable)


class CycleError(ValueError):
    """Raised when a directed acyclic graph operation finds a cycle."""


class DirectedGraph(Generic[T]):
    """Adjacency-map directed graph with deterministic traversal order."""

    def __init__(self) -> None:
        self._adjacency: dict[T, dict[T, None]] = {}

    def add_node(self, node: T) -> None:
        self._adjacency.setdefault(node, {})

    def add_edge(self, source: T, target: T) -> None:
        self.add_node(source)
        self.add_node(target)
        self._adjacency[source][target] = None

    def remove_edge(self, source: T, target: T) -> None:
        try:
            del self._adjacency[source][target]
        except KeyError as exc:
            raise LookupError(f"graph edge not found: {source} -> {target}") from exc

    def remove_node(self, node: T) -> None:
        if node not in self._adjacency:
            raise LookupError(f"graph node not found: {node}")
        del self._adjacency[node]
        for targets in self._adjacency.values():
            targets.pop(node, None)

    @property
    def nodes(self) -> tuple[T, ...]:
        return tuple(self._adjacency)

    def neighbors(self, node: T) -> tuple[T, ...]:
        try:
            return tuple(self._adjacency[node])
        except KeyError as exc:
            raise KeyError(f"unknown graph node: {node}") from exc

    def topological_order(self) -> tuple[T, ...]:
        indegree = dict.fromkeys(self._adjacency, 0)
        for targets in self._adjacency.values():
            for target in targets:
                indegree[target] += 1
        ready = deque(node for node, count in indegree.items() if count == 0)
        ordered: list[T] = []
        while ready:
            node = ready.popleft()
            ordered.append(node)
            for target in self._adjacency[node]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if len(ordered) != len(self._adjacency):
            blocked = tuple(node for node, count in indegree.items() if count > 0)
            raise CycleError(f"dependency cycle contains: {blocked}")
        return tuple(ordered)

    def shortest_path(self, source: T, target: T) -> tuple[T, ...]:
        if source not in self._adjacency or target not in self._adjacency:
            raise KeyError("source and target must be graph nodes")
        queue = deque([source])
        previous: dict[T, T | None] = {source: None}
        while queue:
            node = queue.popleft()
            if node == target:
                path: list[T] = []
                current: T | None = target
                while current is not None:
                    path.append(current)
                    current = previous[current]
                return tuple(reversed(path))
            for neighbor in self._adjacency[node]:
                if neighbor not in previous:
                    previous[neighbor] = node
                    queue.append(neighbor)
        raise LookupError(f"no route from {source} to {target}")

    def breadth_first(self, source: T) -> tuple[T, ...]:
        if source not in self._adjacency:
            raise KeyError(f"unknown graph node: {source}")
        queue = deque([source])
        visited = {source}
        order: list[T] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in self._adjacency[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return tuple(order)

    def depth_first(self, source: T) -> tuple[T, ...]:
        if source not in self._adjacency:
            raise KeyError(f"unknown graph node: {source}")
        stack = [source]
        visited: set[T] = set()
        order: list[T] = []
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            order.append(node)
            stack.extend(reversed(tuple(self._adjacency[node])))
        return tuple(order)

    def standard_library_order(self) -> tuple[T, ...]:
        predecessors: dict[T, set[T]] = {node: set() for node in self._adjacency}
        for source, targets in self._adjacency.items():
            for target in targets:
                predecessors[target].add(source)
        try:
            return tuple(TopologicalSorter(predecessors).static_order())
        except StandardCycleError as exc:
            cycle = tuple(exc.args[1]) if len(exc.args) > 1 else ()
            raise CycleError(f"dependency cycle contains: {cycle}") from exc

    @classmethod
    def from_edges(cls, edges: Iterable[tuple[T, T]]) -> DirectedGraph[T]:
        graph = cls()
        for source, target in edges:
            graph.add_edge(source, target)
        return graph
