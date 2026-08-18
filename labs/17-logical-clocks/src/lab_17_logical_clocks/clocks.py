"""Lamport and vector clock primitives for relay task updates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class ClockRelation(str, Enum):
    """How one logical timestamp relates to another."""

    BEFORE = "before"
    AFTER = "after"
    EQUAL = "equal"
    CONCURRENT = "concurrent"


@dataclass(frozen=True)
class LamportStamp:
    """One Lamport timestamp with a node tie-breaker."""

    counter: int
    node_id: str

    def __post_init__(self) -> None:
        if self.counter < 0:
            raise ValueError("counter must not be negative")
        if not self.node_id.strip():
            raise ValueError("node_id must not be empty")

    def sort_key(self) -> tuple[int, str]:
        return (self.counter, self.node_id)


@dataclass
class LamportClock:
    """Stateful Lamport clock used by one replica."""

    node_id: str
    counter: int = 0

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise ValueError("node_id must not be empty")
        if self.counter < 0:
            raise ValueError("counter must not be negative")

    def local_event(self) -> LamportStamp:
        self.counter += 1
        return LamportStamp(self.counter, self.node_id)

    def observe(self, remote: LamportStamp) -> LamportStamp:
        self.counter = max(self.counter, remote.counter) + 1
        return LamportStamp(self.counter, self.node_id)


@dataclass(frozen=True)
class VectorClock:
    """Immutable vector clock with deterministic key order."""

    entries: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        last_node = ""
        for node_id, counter in self.entries:
            if not node_id.strip():
                raise ValueError("node_id must not be empty")
            if counter < 0:
                raise ValueError("counter must not be negative")
            if last_node and node_id <= last_node:
                raise ValueError("entries must be sorted by node_id without duplicates")
            last_node = node_id

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, int]) -> VectorClock:
        return cls(tuple(sorted(mapping.items())))

    def to_mapping(self) -> dict[str, int]:
        return dict(self.entries)

    def get(self, node_id: str) -> int:
        return self.to_mapping().get(node_id, 0)

    def increment(self, node_id: str) -> VectorClock:
        updated = self.to_mapping()
        updated[node_id] = updated.get(node_id, 0) + 1
        return VectorClock.from_mapping(updated)

    def merge(self, other: VectorClock) -> VectorClock:
        merged: dict[str, int] = {}
        for node_id in sorted(set(self.to_mapping()) | set(other.to_mapping())):
            merged[node_id] = max(self.get(node_id), other.get(node_id))
        return VectorClock.from_mapping(merged)

    def compare(self, other: VectorClock) -> ClockRelation:
        has_less = False
        has_greater = False
        for node_id in sorted(set(self.to_mapping()) | set(other.to_mapping())):
            left = self.get(node_id)
            right = other.get(node_id)
            has_less = has_less or left < right
            has_greater = has_greater or left > right
        if not has_less and not has_greater:
            return ClockRelation.EQUAL
        if has_less and not has_greater:
            return ClockRelation.BEFORE
        if has_greater and not has_less:
            return ClockRelation.AFTER
        return ClockRelation.CONCURRENT
