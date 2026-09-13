# Lab 07 Performance Diagnostics

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
src/lab_07_performance_diagnostics/    the package
  debug_target.py                       stable pdb and post-mortem failure
  timing.py                             injectable monotonic timing
  profiling.py                          structured and saved cProfile data
  benchmarks.py                         pyperf runner entry point
  benchmarking.py                       pyperf artifact interpretation
  probe.py                              observable wait and CPU processes
  commands.py                           side-effect-free Linux command plans
artifacts/relay_probe.c                 native GDB and core exercise
tests/                                  deterministic test suite
```

## Conventions

- Benchmark product behavior with `pyperf`; profile internals with `cProfile`.
  Do not turn timing comparisons into pass/fail quality gates.
- Keep benchmark setup boundaries explicit. The classification pair includes
  per-call set construction; the lookup pair uses prepared containers. Do not
  merge these into one ambiguous measurement.
- A pyperf `bench_time_func` receives its loop count positionally and returns
  total elapsed seconds. Use `time.perf_counter`, not the deprecated pyperf
  alias, and let pyperf normalize by the loop count.
- Preserve raw pyperf JSON, metadata, distributions, and practical effect
  thresholds. A statistically detected difference is not automatically useful
  to the product.
- Linux diagnostic integrations are side-effect-free command plans in the
  deterministic gate. Live GDB, ptrace, perf, or ftrace exercises require the
  reader to inspect scope and permissions first.
- Keep optional probes bounded by a duration. Bind only to loopback, write only
  to reader-selected paths, and never weaken ptrace, perf, or tracefs policy.
- Native diagnostic examples belong in `artifacts/` and are compiled by the
  reader. The normal Python quality gate must not require a C compiler.
- Use monotonic performance counters for elapsed time and injectable clocks in
  tests.
- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
