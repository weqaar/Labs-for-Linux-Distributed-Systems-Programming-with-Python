"""Native and pure-Python framing helpers for relay."""

from __future__ import annotations

from .benchmark import BenchmarkResult, benchmark_frame_digests
from .framing import FrameDigest, describe_frame, render_task_frame
from .models import RelayTask, TaskState
from .native import FramingImplementation, has_native_extension, resolve_implementation
from .pure import HEADER_SIZE, MAX_FRAME_SIZE, encode_frame, fletcher16, parse_length_prefix

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "BenchmarkResult",
    "FrameDigest",
    "FramingImplementation",
    "HEADER_SIZE",
    "MAX_FRAME_SIZE",
    "RelayTask",
    "TaskState",
    "benchmark_frame_digests",
    "describe_frame",
    "encode_frame",
    "fletcher16",
    "has_native_extension",
    "parse_length_prefix",
    "render_task_frame",
    "resolve_implementation",
]
