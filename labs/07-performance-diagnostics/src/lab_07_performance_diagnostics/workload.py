"""Small relay workload used by profilers and benchmarks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class RelayWorkload:
    """Classify task identifiers against the active task set."""

    active_ids: list[str]

    def classify_with_list(self, candidates: list[str]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for candidate in candidates:
            counts["active" if candidate in self.active_ids else "missing"] += 1
        return dict(counts)

    def classify_with_set(self, candidates: list[str]) -> dict[str, int]:
        active = set(self.active_ids)
        counts: Counter[str] = Counter()
        for candidate in candidates:
            counts["active" if candidate in active else "missing"] += 1
        return dict(counts)
