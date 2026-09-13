"""Small link-state routing model using the weighted graph algorithms."""

from __future__ import annotations

from dataclasses import dataclass

from .weighted_graph import ShortestPaths, WeightedGraph, dijkstra


@dataclass(frozen=True, slots=True)
class Route:
    destination: str
    next_hop: str | None
    cost: float
    path: tuple[str, ...]


class OSPFTopology:
    """Area-local topology after link-state advertisements have converged."""

    def __init__(self) -> None:
        self._graph: WeightedGraph[str, None] = WeightedGraph()

    def add_link(
        self,
        left: str,
        right: str,
        left_cost: float,
        right_cost: float | None = None,
    ) -> None:
        reverse_cost = left_cost if right_cost is None else right_cost
        self._graph.add_edge(left, right, left_cost)
        self._graph.add_edge(right, left, reverse_cost)

    def shortest_path_tree(self, root: str) -> ShortestPaths[str]:
        return dijkstra(self._graph, root)

    def routing_table(self, root: str) -> tuple[Route, ...]:
        paths = self.shortest_path_tree(root)
        routes: list[Route] = []
        for destination, cost in paths.distances.items():
            path = paths.path_to(destination)
            next_hop = path[1] if len(path) > 1 else None
            routes.append(Route(destination, next_hop, cost, path))
        return tuple(routes)
