"""Deterministic benchmark helpers for the framing checkpoint."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from .framing import describe_frame
from .native import resolve_implementation
from .pure import HEADER_SIZE, ReadableBuffer, coerce_bytes


@dataclass(frozen=True)
class BenchmarkResult:
    """Measured work for one implementation on a fixed corpus."""

    implementation: str
    frames: int
    bytes_processed: int
    digest_total: int
    elapsed_seconds: float


def benchmark_frame_digests(
    frames: Sequence[ReadableBuffer],
    *,
    repeats: int = 100,
) -> tuple[BenchmarkResult, ...]:
    if repeats <= 0:
        raise ValueError("repeats must be positive")
    raw_frames = [coerce_bytes(frame) for frame in frames]
    implementations = [resolve_implementation(prefer_native=False)]
    preferred = resolve_implementation(prefer_native=True)
    if preferred.name != implementations[0].name:
        implementations.append(preferred)
    results: list[BenchmarkResult] = []
    for implementation in implementations:
        start = time.perf_counter()
        digest_total = 0
        for _ in range(repeats):
            for frame in raw_frames:
                digest_total += describe_frame(frame, implementation=implementation).checksum
        elapsed_seconds = time.perf_counter() - start
        results.append(
            BenchmarkResult(
                implementation=implementation.name,
                frames=len(raw_frames) * repeats,
                bytes_processed=sum(len(frame) - HEADER_SIZE for frame in raw_frames) * repeats,
                digest_total=digest_total,
                elapsed_seconds=elapsed_seconds,
            )
        )
    return tuple(results)
