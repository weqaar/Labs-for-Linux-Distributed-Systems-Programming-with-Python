# Lab 06 Trees And Graphs

Orientation for anyone, human or AI, working in this repository.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Layout

```
src/lab_06_trees_and_graphs/    the package
tests/                the test suite
```

## Conventions

- Keep the red-black tree as an ordered priority index and validate its search,
  color, leaning, and black-height invariants.
- Keep rooted-tree child links and its value index synchronized during replace
  and subtree removal.
- Preserve all three binary-search-tree deletion cases and prove explicit
  rebuilding reduces height for ascending input.
- Keep dependency order and priority order separate. A high priority must not
  bypass a graph dependency.
- Graph traversals must be deterministic and cycles must fail rather than
  returning a partial relay plan.
- Keep `standard_library_order` compatible with `graphlib.TopologicalSorter`.
  Do not confuse the standard module with the separate companion `graph_lib`
  API represented by `WeightedGraphView`.
- Dijkstra must reject negative weights. Bellman-Ford must report reachable
  negative cycles. OSPF examples select accumulated cost, not hop count.
- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
