"""Deterministic elapsed-time measurement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from time import perf_counter_ns
from typing import ParamSpec, Protocol, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class NanosecondClock(Protocol):
    def __call__(self) -> int: ...


@dataclass(frozen=True, slots=True)
class TimingResult:
    elapsed_ns: int


@dataclass(frozen=True, slots=True)
class TimingSummary:
    samples_ns: tuple[int, ...]
    minimum_ns: int
    median_ns: float
    maximum_ns: int


def measure_call(
    function: Callable[P, R],
    clock: NanosecondClock,
    *args: P.args,
    **kwargs: P.kwargs,
) -> tuple[R, TimingResult]:
    """Measure one call with an injectable monotonic performance counter."""

    started = clock()
    result = function(*args, **kwargs)
    finished = clock()
    if finished < started:
        raise ValueError("performance clock moved backwards")
    return result, TimingResult(finished - started)


def measure_once(
    function: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> tuple[R, TimingResult]:
    """Measure one call with the process monotonic performance counter."""

    return measure_call(function, perf_counter_ns, *args, **kwargs)


def measure_repeated(
    function: Callable[P, R],
    clock: NanosecondClock,
    repetitions: int,
    *args: P.args,
    **kwargs: P.kwargs,
) -> tuple[tuple[R, ...], TimingSummary]:
    """Collect teaching samples without claiming to replace a benchmark harness."""

    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    results: list[R] = []
    samples: list[int] = []
    for _ in range(repetitions):
        result, timing = measure_call(function, clock, *args, **kwargs)
        results.append(result)
        samples.append(timing.elapsed_ns)
    return (
        tuple(results),
        TimingSummary(
            samples_ns=tuple(samples),
            minimum_ns=min(samples),
            median_ns=median(samples),
            maximum_ns=max(samples),
        ),
    )
