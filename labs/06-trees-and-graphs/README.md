# Lab 06 Trees and graphs

This lab gives SigRaft three related capabilities: a tree of service names
you can change, ordered indexes of jobs, and graphs for dependencies and
worker routes. The Python import name is `relay`. That name does not mean
the program relays traffic. The code calls a job identifier `task_id`.
Each exercise checks a different property. A priority index must not be used as a
dependency plan, and a fewest-hop route must not be presented as a
lowest-cost route.

## Goal and technologies

Use the supplied reference structures to answer three different questions:
where a value lives, which work is eligible, and which route costs least.
You will mutate structures and check invariants, not deploy a scheduler or
routing daemon. Python 3.10 or later, standard-library `graphlib` and the
declared typing support are enough; no graph service or network is required.
Read [`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`, then
work from this lab directory. All dependencies are in `pyproject.toml`.

## Set up the lab

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

## Exercise 1 Build a rooted tree

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

## Exercise 2 Observe and repair a search-tree shape

The service tree groups named components. A search tree answers a different
question: where is the value for an ordered key? Its shape determines how many
nodes a lookup may visit.

Insert keys 1 through 7 into `BinarySearchTree` in ascending order. The result
has height six because every node has one right child. Check the four traversal
methods, then call `rebalance`. The rebuilt tree has key 4 at its root and
height two.

Build the example containing keys `8, 3, 10, 1, 6, 14, 4, 7, 13`. Delete key 3,
which has two children. Verify that the in-order result remains sorted and that
the associated value returned by `delete` belongs to key 3.

## Exercise 3 Maintain red-black invariants

Rebuilding repairs the previous tree after insertion. A red-black tree instead
adjusts its shape during insertion. Colors record balance rules; rotations
change links while preserving key order.

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

## Exercise 4 Traverse and mutate directed graphs

Build a `DirectedGraph` for an ingress node, routers, and workers. Compare
depth-first and breadth-first traversal. Remove one edge and one vertex, then
verify that removing the vertex also removed incoming references from other
adjacency entries.

Build a directed acyclic graph (DAG) of job dependencies and compare
`topological_order` with `standard_library_order`, which uses Python's
`graphlib.TopologicalSorter`. Add a back edge and confirm that both the lab's
implementation and the standard library report a cycle rather than a partial plan.

## Exercise 5 Select a weighted shortest-path algorithm

A fewest-hop route counts edges; a weighted route adds their costs. The lab's
`WeightedGraph` supplies nodes, neighbors and edge weights so you can compare
those questions. Its interface also resembles the companion `graph_lib`
example, but that separate checkout is not needed.

Create edges A to B with cost 10, B to C with cost 5, and A to C with cost 20.
Dijkstra must choose A, B, C at total cost 15. Add a negative edge and confirm
that Dijkstra rejects it.

Build a separate graph with A to B at 4, A to C at 5, and B to C at -2.
Bellman-Ford must find cost 2 to C. Add C to A at -3 and confirm that it raises
`NegativeCycleError`.

## Exercise 6 Derive an OSPF-style routing table

Open Shortest Path First (OSPF) computes routes from known link costs. Use that
routing problem to apply the weighted search above: several low-cost links
can beat one expensive direct link. Build these undirected links:

```text
R1 to R2  10
R2 to R5   1
R1 to R3   2
R2 to R4   2
R3 to R4   2
```

Run `routing_table("R1")`. R2 must use next hop R3 and cost 6, even though R2
has a direct one-hop link. R5 must have cost 7. This model represents the
shortest-path calculation after link-state information has converged; it does
not model advertisement flooding or failure detection.

## Exercise 7 Keep scheduling questions separate

Add three tasks to `RelayPlan` so a high-priority task depends on a
lower-priority task. `priority_order` sorts all tasks by preference; it does
not filter out tasks with unfinished predecessors. `execution_order` preserves
dependencies. A scheduler must determine eligibility before using priority
to choose among ready tasks.

## Completion condition

The lab is complete when tree mutation preserves child links and lookup
indexes, binary-search-tree deletion and rebuilding preserve ordering,
red-black checks preserve every balance rule, graph traversals are
deterministic, cycles and unreachable routes are explicit errors, each
shortest-path method enforces its weight assumptions, the OSPF example selects
cost rather than hop count, and `pybootstrap check` exits zero.

These exercises show how to index priorities, preserve job dependencies and
calculate routes for SigRaft. Lab 39 does not import these tree and graph
implementations.

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
>>> tree = lab.Tree("relay")
>>> tree.add("relay", "workers")
>>> tree.preorder(), tree.height()
(('relay', 'workers'), 1)
>>> inspect.signature(tree.remove_subtree)
```

Construct the smallest tree or graph in the exercise. Inspect its type, root or
vertices, method signatures, and traversal result. Compare the object graph
with the AST tree in Lab 10.

Run `pytest -q tests/test_lab_06_trees_and_graphs.py` while experimenting, then
require `pybootstrap check` exit 0. Exit 1 means findings; exit 2 means a
gate could not run. Be able to explain why sorted output alone does not prove
balance or dependency safety. Exit Python to discard the structures; no
external resources need cleanup.
