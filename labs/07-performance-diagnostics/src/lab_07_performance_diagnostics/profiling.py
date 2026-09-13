"""Deterministic cProfile summaries for relay callables."""

from __future__ import annotations

import cProfile
import pstats
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class ProfileRow:
    function: str
    calls: int
    total_seconds: float
    cumulative_seconds: float


def profile_call(
    function: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> tuple[R, tuple[ProfileRow, ...]]:
    """Run a callable under cProfile and return structured rows."""

    profiler = cProfile.Profile()
    result = profiler.runcall(function, *args, **kwargs)
    rows = tuple(
        sorted(
            (
                ProfileRow(
                    function=_function_name(entry.code),
                    calls=entry.callcount,
                    total_seconds=entry.inlinetime,
                    cumulative_seconds=entry.totaltime,
                )
                for entry in profiler.getstats()
            ),
            key=lambda row: row.cumulative_seconds,
            reverse=True,
        )
    )
    return result, rows


def profile_to_file(
    output: Path,
    function: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Profile a call and save a pstats-compatible artifact."""

    profiler = cProfile.Profile()
    result = profiler.runcall(function, *args, **kwargs)
    profiler.dump_stats(output)
    return result


def read_profile(
    profile: Path,
    *,
    sort_by: pstats.SortKey = pstats.SortKey.CUMULATIVE,
) -> tuple[ProfileRow, ...]:
    """Read a saved profile and return rows in the requested pstats order."""

    stats = pstats.Stats(str(profile)).get_stats_profile()
    rows: list[ProfileRow] = []
    for function, entry in stats.func_profiles.items():
        total_calls = int(entry.ncalls.split("/", maxsplit=1)[0])
        rows.append(
            ProfileRow(
                function=(
                    f"{entry.file_name}:{entry.line_number}:{function} [{entry.ncalls} calls]"
                ),
                calls=total_calls,
                total_seconds=entry.tottime,
                cumulative_seconds=entry.cumtime,
            )
        )
    if sort_by == pstats.SortKey.TIME:
        rows.sort(key=lambda row: row.total_seconds, reverse=True)
    else:
        rows.sort(key=lambda row: row.cumulative_seconds, reverse=True)
    return tuple(rows)


def _function_name(code: str | types.CodeType) -> str:
    if isinstance(code, str):
        return code
    return f"{code.co_filename}:{code.co_firstlineno}:{code.co_name}"
