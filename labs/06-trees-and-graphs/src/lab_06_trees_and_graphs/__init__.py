"""Tree and graph algorithms applied to relay."""

from __future__ import annotations

from .basic_tree import BinarySearchTree, Tree, TreeNode
from .graph import CycleError, DirectedGraph
from .planner import RelayPlan, TaskSpec
from .red_black import RedBlackTree
from .routing import OSPFTopology, Route
from .weighted_graph import (
    NegativeCycleError,
    ShortestPaths,
    WeightedGraph,
    bellman_ford,
    breadth_first,
    depth_first,
    dijkstra,
)

__version__ = "0.1.0"

__all__ = [
    "BinarySearchTree",
    "CycleError",
    "DirectedGraph",
    "NegativeCycleError",
    "OSPFTopology",
    "RedBlackTree",
    "RelayPlan",
    "Route",
    "ShortestPaths",
    "TaskSpec",
    "Tree",
    "TreeNode",
    "WeightedGraph",
    "__version__",
    "bellman_ford",
    "breadth_first",
    "depth_first",
    "dijkstra",
]
