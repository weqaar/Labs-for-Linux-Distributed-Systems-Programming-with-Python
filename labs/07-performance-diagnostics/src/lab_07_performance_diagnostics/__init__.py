"""Debugging, profiling, and benchmarking tools for relay."""

from __future__ import annotations

from .benchmarking import (
    BenchmarkComparison,
    BenchmarkSummary,
    compare_medians,
    summarize_result,
)
from .commands import DiagnosticPlan
from .inspection import (
    BytecodeRow,
    ObjectSnapshot,
    inspect_bytecode,
    inspect_mapping,
    inspect_object,
)
from .probe import cpu_work
from .profiling import ProfileRow, profile_call, profile_to_file, read_profile
from .timing import TimingResult, TimingSummary, measure_call, measure_once, measure_repeated
from .workload import RelayWorkload

__version__ = "0.1.0"

__all__ = [
    "BenchmarkComparison",
    "BenchmarkSummary",
    "BytecodeRow",
    "DiagnosticPlan",
    "ObjectSnapshot",
    "ProfileRow",
    "RelayWorkload",
    "TimingResult",
    "TimingSummary",
    "__version__",
    "compare_medians",
    "cpu_work",
    "inspect_bytecode",
    "inspect_mapping",
    "inspect_object",
    "measure_call",
    "measure_once",
    "measure_repeated",
    "profile_call",
    "profile_to_file",
    "read_profile",
    "summarize_result",
]
