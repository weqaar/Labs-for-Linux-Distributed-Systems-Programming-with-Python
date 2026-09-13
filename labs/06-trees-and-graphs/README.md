# Lab 06 Trees and graphs

This checkpoint gives `relay` three related capabilities: a mutable service
tree, ordered task indexes, and graphs for dependencies and worker routes. Each
exercise states a different invariant. A priority index must not be used as a
dependency plan, and a fewest-hop route must not be presented as a
lowest-cost route.

## Set up the checkpoint

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

## Exercise 1: Build a rooted tree

Open `basic_tree.py`. Construct this hierarchy with `Tree.add`:

```text
relay
├── ingress
│   └── auth
└── workers
    ├── worker-a
    └── worker-b
```

Predict the pre-order, post-order, and breadth-first sequences before running
the tests. Confirm that the height is two. Replace `worker-b` with `worker-c`,
then remove the `workers` subtree. The returned pre-order sequence must name
every removed node, and `find` must no longer return any of them.

The child links and the lookup dictionary form one data structure. A mutation
is incomplete if it updates only one of them.

## Exercise 2: Observe and repair a search-tree shape

Insert keys 1 through 7 into `BinarySearchTree` in ascending order. The result
has height six because every node has one right child. Check the four traversal
methods, then call `rebalance`. The rebuilt tree has key 4 at its root and
height two.

Build the example containing keys `8, 3, 10, 1, 6, 14, 4, 7, 13`. Delete key 3,
which has two children. Verify that the in-order result remains sorted and that
the associated value returned by `delete` belongs to key 3.

## Exercise 3: Maintain red-black invariants

Trace insertions that trigger:

1. A left rotation for a red right link.
2. A right rotation for two consecutive red left links.
3. A color flip when both child links are red.

After every insertion, call `validate`. Sorted output alone is not enough. The
root must be black, red links must lean left, no red node may have a red child,
and every root-to-missing-leaf path must have equal black height. Duplicate
keys update values without increasing the size.

`RelayPlan` stores `(-priority, task_id)` in this tree. Negation puts higher
priority first, and the identifier gives ties a stable order.

## Exercise 4: Traverse and mutate directed graphs

Build a `DirectedGraph` for an ingress node, routers, and workers. Compare
depth-first and breadth-first traversal. Remove one edge and one vertex, then
verify that removing the vertex also removed incoming references from other
adjacency entries.

Build a dependency DAG and compare `topological_order` with
`standard_library_order`, which uses Python's
`graphlib.TopologicalSorter`. Add a back edge and confirm that both the custom
contract and the standard library report a cycle rather than a partial plan.

## Exercise 5: Select a weighted shortest-path algorithm

`WeightedGraph` follows the structural operations used by the companion
`graph_lib` example: enumerate nodes, enumerate neighbors, and retrieve an edge
weight. The lab owns its implementation and does not require that separate
checkout.

Create edges A to B with cost 10, B to C with cost 5, and A to C with cost 20.
Dijkstra must choose A, B, C at total cost 15. Add a negative edge and confirm
that Dijkstra rejects it.

Build a separate graph with A to B at 4, A to C at 5, and B to C at -2.
Bellman-Ford must find cost 2 to C. Add C to A at -3 and confirm that it raises
`NegativeCycleError`.

## Exercise 6: Derive an OSPF-style routing table

Build the topology from the chapter:

```text
R1 --10-- R2 --1-- R5
 |         |
 2         2
 |         |
R3 --2--- R4
```

Run `routing_table("R1")`. R2 must use next hop R3 and cost 6, even though R2
has a direct one-hop link. R5 must have cost 7. This model represents the
shortest-path calculation after link-state information has converged; it does
not model advertisement flooding or failure detection.

## Exercise 7: Keep scheduling questions separate

Add three tasks to `RelayPlan` so a high-priority task depends on a
lower-priority task. `priority_order` describes preference among eligible work.
`execution_order` preserves dependencies. A scheduler may use both, but
priority must never bypass an unfinished predecessor.

## Completion condition

The checkpoint is complete when tree mutation preserves child links and lookup
indexes, binary-search-tree deletion and rebuilding preserve ordering,
red-black checks preserve every balance rule, graph traversals are
deterministic, cycles and unreachable routes are explicit errors, each
shortest-path method enforces its weight assumptions, the OSPF example selects
cost rather than hop count, and `pybootstrap check` exits zero.

The final `relay` product uses these structures to order pending tasks, validate
orchestration dependencies, and reason about service topology.
## Python REPL debugging session

After the editable install, inspect the package and one tree object:

```pycon
>>> import inspect
>>> import lab_06_trees_and_graphs as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Construct the smallest tree or graph in the exercise. Inspect its type, root or
vertices, method signatures, and traversal result. Compare the object graph
with the AST tree in Lab 10.
