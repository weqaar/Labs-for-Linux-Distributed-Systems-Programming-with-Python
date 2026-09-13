"""Weighted graph algorithms compatible with the companion graph_lib API."""

from __future__ import annotations

from collections import deque
from collections.abc import Hashable, Iterator
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from typing import Generic, Protocol, TypeVar

T = TypeVar("T", bound=Hashable)
D = TypeVar("D")


class WeightedGraphView(Protocol[T]):
    """Structural subset shared with the companion ``graph_lib`` Graph."""

    def __contains__(self, node: object) -> bool: ...

    def get_all_nodes(self) -> Iterator[T]: ...

    def neighbors(self, node: T) -> Iterator[T]: ...

    def get_edge_weight(self, source: T, target: T) -> float | None: ...


class NegativeCycleError(ValueError):
    """Raised when no finite shortest path exists because of a negative cycle."""


class WeightedGraph(Generic[T, D]):
    """Directed weighted adjacency-map graph with optional node data."""

    def __init__(self) -> None:
        self._nodes: dict[T, D | None] = {}
        self._adjacency: dict[T, dict[T, float]] = {}

    def __contains__(self, node: object) -> bool:
        return node in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def add_node(self, node: T, data: D | None = None) -> None:
        if node in self._nodes:
            raise ValueError(f"graph node already exists: {node}")
        self._nodes[node] = data
        self._adjacency[node] = {}

    def add_edge(self, source: T, target: T, weight: float = 1.0) -> None:
        if source not in self._nodes:
            self.add_node(source)
        if target not in self._nodes:
            self.add_node(target)
        self._adjacency[source][target] = float(weight)

    def remove_edge(self, source: T, target: T) -> None:
        try:
            del self._adjacency[source][target]
        except KeyError as exc:
            raise LookupError(f"graph edge not found: {source} -> {target}") from exc

    def remove_node(self, node: T) -> None:
        if node not in self._nodes:
            raise LookupError(f"graph node not found: {node}")
        del self._nodes[node]
        del self._adjacency[node]
        for neighbors in self._adjacency.values():
            neighbors.pop(node, None)

    def get_node_data(self, node: T) -> D | None:
        try:
            return self._nodes[node]
        except KeyError as exc:
            raise LookupError(f"graph node not found: {node}") from exc

    def get_all_nodes(self) -> Iterator[T]:
        return iter(self._nodes)

    def neighbors(self, node: T) -> Iterator[T]:
        try:
            return iter(self._adjacency[node])
        except KeyError as exc:
            raise LookupError(f"graph node not found: {node}") from exc

    def get_edge_weight(self, source: T, target: T) -> float | None:
        return self._adjacency.get(source, {}).get(target)

    def edges(self) -> tuple[tuple[T, T, float], ...]:
        return tuple(
            (source, target, weight)
            for source, neighbors in self._adjacency.items()
            for target, weight in neighbors.items()
        )


@dataclass(frozen=True, slots=True)
class ShortestPaths(Generic[T]):
    """Distances and predecessor tree rooted at one source."""

    source: T
    distances: dict[T, float]
    predecessors: dict[T, T | None]

    def path_to(self, target: T) -> tuple[T, ...]:
        if target not in self.distances:
            raise LookupError(f"graph target is unreachable: {target}")
        path: list[T] = []
        current: T | None = target
        while current is not None:
            path.append(current)
            current = self.predecessors[current]
        return tuple(reversed(path))


def dijkstra(graph: WeightedGraphView[T], source: T) -> ShortestPaths[T]:
    """Find shortest paths from source when every edge weight is non-negative."""

    if source not in graph:
        raise LookupError(f"graph node not found: {source}")
    distances: dict[T, float] = {source: 0.0}
    predecessors: dict[T, T | None] = {source: None}
    sequence = count()
    queue: list[tuple[float, int, T]] = [(0.0, next(sequence), source)]
    while queue:
        distance, _, node = heappop(queue)
        if distance != distances[node]:
            continue
        for neighbor in graph.neighbors(node):
            weight = graph.get_edge_weight(node, neighbor)
            if weight is None:
                raise AssertionError("neighbor must have an edge weight")
            if weight < 0:
                raise ValueError("Dijkstra requires non-negative edge weights")
            candidate = distance + weight
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                predecessors[neighbor] = node
                heappush(queue, (candidate, next(sequence), neighbor))
    return ShortestPaths(source, distances, predecessors)


def bellman_ford(graph: WeightedGraph[T, D], source: T) -> ShortestPaths[T]:
    """Find shortest paths with negative edges and reject reachable negative cycles."""

    nodes = tuple(graph.get_all_nodes())
    if source not in graph:
        raise LookupError(f"graph node not found: {source}")
    distances = dict.fromkeys(nodes, float("inf"))
    predecessors: dict[T, T | None] = {source: None}
    distances[source] = 0.0
    for _ in range(max(0, len(nodes) - 1)):
        changed = False
        for start, end, weight in graph.edges():
            if distances[start] == float("inf"):
                continue
            candidate = distances[start] + weight
            if candidate < distances[end]:
                distances[end] = candidate
                predecessors[end] = start
                changed = True
        if not changed:
            break
    for start, end, weight in graph.edges():
        if distances[start] != float("inf") and distances[start] + weight < distances[end]:
            raise NegativeCycleError("reachable negative cycle has no finite shortest path")
    reachable = {node: distance for node, distance in distances.items() if distance < float("inf")}
    return ShortestPaths(source, reachable, predecessors)


def breadth_first(graph: WeightedGraphView[T], source: T) -> tuple[T, ...]:
    """Return reachable vertices in increasing unweighted hop distance."""

    if source not in graph:
        raise LookupError(f"graph node not found: {source}")
    queue = deque([source])
    visited = {source}
    order: list[T] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.neighbors(node):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return tuple(order)


def depth_first(graph: WeightedGraphView[T], source: T) -> tuple[T, ...]:
    """Return a deterministic iterative depth-first preorder."""

    if source not in graph:
        raise LookupError(f"graph node not found: {source}")
    stack = [source]
    visited: set[T] = set()
    order: list[T] = []
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        order.append(node)
        stack.extend(reversed(tuple(graph.neighbors(node))))
    return tuple(order)
