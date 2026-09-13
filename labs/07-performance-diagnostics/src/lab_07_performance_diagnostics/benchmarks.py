"""pyperf entry point for the relay membership benchmark."""

from __future__ import annotations

from time import perf_counter

import pyperf

from lab_07_performance_diagnostics.workload import RelayWorkload


def benchmark_classification(loops: int, use_set: bool) -> float:
    """Measure the complete operation, including construction of its lookup set."""

    workload = RelayWorkload([f"task-{index}" for index in range(1, 1001)])
    candidates = [f"task-{index}" for index in range(501, 1501)]
    method = workload.classify_with_set if use_set else workload.classify_with_list
    started = perf_counter()
    for _ in range(loops):
        method(candidates)
    return perf_counter() - started


def benchmark_lookup(loops: int, container: list[str] | set[str], target: str) -> float:
    """Measure steady-state membership after the container has been prepared."""

    started = perf_counter()
    for _ in range(loops):
        target in container
    return perf_counter() - started


def main() -> None:
    runner = pyperf.Runner()
    runner.metadata["relay_input_size"] = 1000
    runner.metadata["relay_match_rate"] = "50 percent"
    runner.bench_time_func(
        "relay_classification_list",
        benchmark_classification,
        False,
    )
    runner.bench_time_func(
        "relay_classification_set_rebuilt",
        benchmark_classification,
        True,
    )
    active = [f"task-{index}" for index in range(1, 1001)]
    runner.bench_time_func("relay_lookup_list", benchmark_lookup, active, "task-1000")
    runner.bench_time_func("relay_lookup_set", benchmark_lookup, set(active), "task-1000")


if __name__ == "__main__":
    main()
