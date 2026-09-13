"""Tests for relay tree and graph algorithms."""

from __future__ import annotations

import pytest

from lab_06_trees_and_graphs import (
    BinarySearchTree,
    CycleError,
    DirectedGraph,
    NegativeCycleError,
    OSPFTopology,
    RedBlackTree,
    RelayPlan,
    TaskSpec,
    Tree,
    WeightedGraph,
    __version__,
    bellman_ford,
    breadth_first,
    depth_first,
    dijkstra,
)


def test_general_tree_defines_structure_and_three_traversals() -> None:
    tree = Tree("relay")
    tree.add("relay", "ingress")
    tree.add("relay", "workers")
    tree.add("ingress", "auth")
    tree.add("workers", "worker-a")
    tree.add("workers", "worker-b")

    assert tree.preorder() == (
        "relay",
        "ingress",
        "auth",
        "workers",
        "worker-a",
        "worker-b",
    )
    assert tree.postorder() == (
        "auth",
        "ingress",
        "worker-a",
        "worker-b",
        "workers",
        "relay",
    )
    assert tree.breadth_first() == (
        "relay",
        "ingress",
        "workers",
        "auth",
        "worker-a",
        "worker-b",
    )
    assert tree.height() == 2


def test_general_tree_replaces_values_and_removes_subtrees() -> None:
    tree = Tree("relay")
    tree.add("relay", "workers")
    tree.add("workers", "worker-a")
    tree.add("workers", "worker-b")

    tree.replace("worker-b", "worker-c")
    removed = tree.remove_subtree("workers")

    assert removed == ("workers", "worker-a", "worker-c")
    assert tree.preorder() == ("relay",)
    with pytest.raises(LookupError):
        tree.find("worker-c")


def test_binary_search_tree_traversals_and_two_child_deletion() -> None:
    tree: BinarySearchTree[int, str] = BinarySearchTree()
    for key in (8, 3, 10, 1, 6, 14, 4, 7, 13):
        tree.put(key, f"task-{key}")

    assert tree.inorder() == tuple((key, f"task-{key}") for key in (1, 3, 4, 6, 7, 8, 10, 13, 14))
    assert tree.preorder()[0] == 8
    assert tree.postorder()[-1] == 8
    assert tree.level_order()[:3] == (8, 3, 10)
    assert tree.delete(3) == "task-3"
    assert tuple(key for key, _ in tree.inorder()) == (1, 4, 6, 7, 8, 10, 13, 14)


def test_binary_search_tree_replacement_and_rebalancing_reduce_height() -> None:
    tree: BinarySearchTree[int, str] = BinarySearchTree()
    for key in range(1, 8):
        tree.put(key, f"task-{key}")
    assert tree.height() == 6

    tree.replace_key(7, 9)
    tree.rebalance()

    assert tree.get(9) == "task-7"
    assert tree.height() == 2
    assert tree.level_order()[0] == 4


def test_red_black_tree_keeps_sorted_items_and_invariants() -> None:
    tree: RedBlackTree[int, str] = RedBlackTree()
    for key in (8, 3, 10, 1, 6, 14, 4, 7, 13):
        tree.put(key, f"task-{key}")

    assert tree.items() == tuple(
        (key, f"task-{key}") for key in sorted({8, 3, 10, 1, 6, 14, 4, 7, 13})
    )
    assert tree.get(6) == "task-6"
    assert tree.validate() >= 3


def test_red_black_tree_updates_without_growing() -> None:
    tree: RedBlackTree[int, str] = RedBlackTree()
    tree.put(5, "old")
    tree.put(5, "new")

    assert len(tree) == 1
    assert tree.get(5) == "new"
    with pytest.raises(KeyError):
        tree.get(6)


def test_graph_topological_order_preserves_dependencies() -> None:
    graph = DirectedGraph.from_edges(
        [
            ("task-1", "task-3"),
            ("task-2", "task-3"),
            ("task-3", "task-4"),
        ]
    )

    order = graph.topological_order()

    assert order.index("task-1") < order.index("task-3")
    assert order.index("task-2") < order.index("task-3")
    assert order.index("task-3") < order.index("task-4")
    assert graph.standard_library_order() == order


def test_graph_reports_cycles_instead_of_returning_a_partial_plan() -> None:
    graph = DirectedGraph.from_edges([("task-1", "task-2"), ("task-2", "task-1")])

    with pytest.raises(CycleError, match="task-1"):
        graph.topological_order()


def test_breadth_first_search_finds_the_fewest_hop_relay_route() -> None:
    graph = DirectedGraph.from_edges(
        [
            ("edge-a", "worker-a"),
            ("edge-a", "router-b"),
            ("router-b", "worker-b"),
            ("worker-a", "worker-b"),
        ]
    )

    assert graph.shortest_path("edge-a", "worker-b") == (
        "edge-a",
        "worker-a",
        "worker-b",
    )
    assert graph.breadth_first("edge-a") == (
        "edge-a",
        "worker-a",
        "router-b",
        "worker-b",
    )
    assert graph.depth_first("edge-a") == (
        "edge-a",
        "worker-a",
        "worker-b",
        "router-b",
    )


def test_graph_mutation_removes_incoming_and_outgoing_edges() -> None:
    graph = DirectedGraph.from_edges([("a", "b"), ("b", "c"), ("c", "a")])

    graph.remove_edge("c", "a")
    graph.remove_node("b")

    assert graph.nodes == ("a", "c")
    assert graph.neighbors("a") == ()


def test_weighted_graph_matches_companion_graphlib_operations() -> None:
    graph: WeightedGraph[str, dict[str, str]] = WeightedGraph()
    graph.add_node("A", {"role": "edge"})
    graph.add_edge("A", "B", 10)
    graph.add_edge("B", "C", 5)
    graph.add_edge("A", "C", 20)

    paths = dijkstra(graph, "A")

    assert graph.get_node_data("A") == {"role": "edge"}
    assert paths.distances["C"] == 15
    assert paths.path_to("C") == ("A", "B", "C")
    assert breadth_first(graph, "A") == ("A", "B", "C")
    assert depth_first(graph, "A") == ("A", "B", "C")


def test_dijkstra_rejects_negative_edges() -> None:
    graph: WeightedGraph[str, None] = WeightedGraph()
    graph.add_edge("A", "B", -1)

    with pytest.raises(ValueError, match="non-negative"):
        dijkstra(graph, "A")


def test_bellman_ford_handles_negative_edges_and_rejects_negative_cycles() -> None:
    graph: WeightedGraph[str, None] = WeightedGraph()
    graph.add_edge("A", "B", 4)
    graph.add_edge("A", "C", 5)
    graph.add_edge("B", "C", -2)

    paths = bellman_ford(graph, "A")

    assert paths.distances["C"] == 2
    assert paths.path_to("C") == ("A", "B", "C")

    graph.add_edge("C", "A", -3)
    with pytest.raises(NegativeCycleError, match="negative cycle"):
        bellman_ford(graph, "A")


def test_ospf_shortest_path_tree_selects_cost_not_hop_count() -> None:
    topology = OSPFTopology()
    topology.add_link("R1", "R2", 10)
    topology.add_link("R1", "R3", 2)
    topology.add_link("R3", "R4", 2)
    topology.add_link("R4", "R2", 2)
    topology.add_link("R2", "R5", 1)

    routes = {route.destination: route for route in topology.routing_table("R1")}

    assert routes["R2"].path == ("R1", "R3", "R4", "R2")
    assert routes["R2"].next_hop == "R3"
    assert routes["R2"].cost == 6
    assert routes["R5"].cost == 7


def test_relay_plan_uses_dependencies_and_priority_for_different_questions() -> None:
    plan = RelayPlan()
    plan.add(TaskSpec("task-1", "fetch", 1))
    plan.add(TaskSpec("task-2", "index", 10), after=("task-1",))
    plan.add(TaskSpec("task-3", "notify", 5), after=("task-2",))

    assert plan.execution_order() == ("task-1", "task-2", "task-3")
    assert plan.priority_order() == ("task-2", "task-3", "task-1")


def test_relay_plan_rejects_an_unknown_dependency() -> None:
    plan = RelayPlan()

    with pytest.raises(LookupError, match="task-99"):
        plan.add(TaskSpec("task-1", "index", 1), after=("task-99",))


def test_version_is_exposed() -> None:
    assert __version__
