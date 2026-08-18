"""Consistent hash ring and replicated relay task store."""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from dataclasses import dataclass
from itertools import count

from lab_23_partitioned_store.contract import RelayTaskRecord, TaskKey


class TaskNotFoundError(Exception):
    """Raised when a read quorum cannot see any version for a key."""


class UnknownVersionError(Exception):
    """Raised when a write references a version the store does not know."""


def stable_hash(value: str) -> int:
    """Return a process-stable integer hash."""
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


@dataclass(frozen=True)
class QuorumConfig:
    """Replication and quorum settings."""

    replica_count: int
    read_quorum: int
    write_quorum: int

    def __post_init__(self) -> None:
        if self.replica_count < 1:
            raise ValueError("replica_count must be positive")
        if not 1 <= self.read_quorum <= self.replica_count:
            raise ValueError("read_quorum must be within replica_count")
        if not 1 <= self.write_quorum <= self.replica_count:
            raise ValueError("write_quorum must be within replica_count")


@dataclass(frozen=True)
class TaskVersion:
    """One immutable version of a task record."""

    version_id: str
    parents: frozenset[str]
    record: RelayTaskRecord


@dataclass(frozen=True)
class WriteResult:
    """Observable write outcome."""

    owners: tuple[str, ...]
    acknowledged_by: tuple[str, ...]
    pending_replicas: tuple[str, ...]
    version: TaskVersion


@dataclass(frozen=True)
class ReadResult:
    """Visible versions returned by a read quorum."""

    owners: tuple[str, ...]
    contacted: tuple[str, ...]
    versions: tuple[TaskVersion, ...]

    @property
    def is_concurrent(self) -> bool:
        return len(self.versions) > 1


@dataclass(frozen=True)
class DistributionReport:
    """Key spread across primary owners."""

    counts: dict[str, int]
    ideal: float
    max_deviation_ratio: float


@dataclass(frozen=True)
class MovementReport:
    """Primary-key movement after a ring change."""

    moved_keys: int
    total_keys: int
    movement_ratio: float


@dataclass(frozen=True)
class _RingPoint:
    point: int
    node_id: str


class ConsistentHashRing:
    """Consistent hash ring with virtual nodes."""

    def __init__(self, node_ids: tuple[str, ...] | list[str], *, virtual_nodes: int = 256) -> None:
        if virtual_nodes < 1:
            raise ValueError("virtual_nodes must be positive")
        unique_nodes = tuple(dict.fromkeys(node_ids))
        if len(unique_nodes) != len(tuple(node_ids)):
            raise ValueError("node_ids must be unique")
        if not unique_nodes:
            raise ValueError("node_ids must not be empty")
        self.node_ids = unique_nodes
        self.virtual_nodes = virtual_nodes
        points = [
            _RingPoint(point=stable_hash(f"{node_id}#{index}"), node_id=node_id)
            for node_id in unique_nodes
            for index in range(virtual_nodes)
        ]
        self._points = tuple(sorted(points, key=lambda entry: entry.point))
        self._point_values = tuple(entry.point for entry in self._points)

    def owners_for_key(self, key: TaskKey | str, count: int) -> tuple[str, ...]:
        """Return *count* distinct owners for the key."""
        if count < 1:
            raise ValueError("count must be positive")
        if count > len(self.node_ids):
            raise ValueError("count exceeds node count")
        lookup = key if isinstance(key, str) else key.ring_key()
        start = bisect_right(self._point_values, stable_hash(lookup))
        owners: list[str] = []
        index = start
        while len(owners) < count:
            point = self._points[index % len(self._points)]
            if point.node_id not in owners:
                owners.append(point.node_id)
            index += 1
        return tuple(owners)

    def measure_distribution(self, keys: tuple[TaskKey, ...] | list[TaskKey]) -> DistributionReport:
        """Measure primary-owner balance for the given keys."""
        counts = {node_id: 0 for node_id in self.node_ids}
        for key in keys:
            counts[self.owners_for_key(key, 1)[0]] += 1
        total_keys = len(keys)
        ideal = total_keys / len(self.node_ids)
        deviation = 0.0
        if ideal:
            deviation = max(abs(count - ideal) / ideal for count in counts.values())
        return DistributionReport(counts=counts, ideal=ideal, max_deviation_ratio=deviation)

    def measure_movement(
        self,
        keys: tuple[TaskKey, ...] | list[TaskKey],
        other: ConsistentHashRing,
    ) -> MovementReport:
        """Measure how many primary owners move between two rings."""
        moved = sum(
            1 for key in keys if self.owners_for_key(key, 1)[0] != other.owners_for_key(key, 1)[0]
        )
        total_keys = len(keys)
        ratio = moved / total_keys if total_keys else 0.0
        return MovementReport(moved_keys=moved, total_keys=total_keys, movement_ratio=ratio)


@dataclass
class _ReplicaNode:
    node_id: str

    def __post_init__(self) -> None:
        self._store: dict[TaskKey, dict[str, TaskVersion]] = {}

    def apply(self, version: TaskVersion) -> None:
        bucket = self._store.setdefault(version.record.key, {})
        bucket[version.version_id] = version

    def versions_for(self, key: TaskKey) -> tuple[TaskVersion, ...]:
        versions = self._store.get(key, {})
        return tuple(sorted(versions.values(), key=lambda version: version.version_id))


class ReplicatedTaskStore:
    """Replicated task store with deterministic weak and strong quorum tests."""

    def __init__(
        self,
        node_ids: tuple[str, ...] | list[str],
        quorum: QuorumConfig,
        *,
        virtual_nodes: int = 256,
    ) -> None:
        if quorum.replica_count > len(tuple(node_ids)):
            raise ValueError("replica_count exceeds node count")
        self.ring = ConsistentHashRing(node_ids, virtual_nodes=virtual_nodes)
        self.quorum = quorum
        self._nodes = {node_id: _ReplicaNode(node_id=node_id) for node_id in self.ring.node_ids}
        self._pending_replication: list[tuple[str, TaskVersion]] = []
        self._version_counter = count(1)

    def write_task(
        self,
        record: RelayTaskRecord,
        *,
        observed_versions: tuple[str, ...] | list[str] = (),
    ) -> WriteResult:
        """Write a new immutable version and acknowledge only the write quorum."""
        owners = self.ring.owners_for_key(record.key, self.quorum.replica_count)
        known_versions = self._known_version_ids(record.key)
        unknown = set(observed_versions) - known_versions
        if unknown:
            raise UnknownVersionError(",".join(sorted(unknown)))
        version = TaskVersion(
            version_id=f"v{next(self._version_counter):08d}",
            parents=frozenset(observed_versions),
            record=record,
        )
        acknowledged = owners[: self.quorum.write_quorum]
        pending = owners[self.quorum.write_quorum :]
        for node_id in acknowledged:
            self._nodes[node_id].apply(version)
        for node_id in pending:
            self._pending_replication.append((node_id, version))
        return WriteResult(
            owners=owners,
            acknowledged_by=acknowledged,
            pending_replicas=pending,
            version=version,
        )

    def replicate_pending(self) -> int:
        """Apply all queued asynchronous replications."""
        pending = list(self._pending_replication)
        self._pending_replication.clear()
        for node_id, version in pending:
            self._nodes[node_id].apply(version)
        return len(pending)

    def pending_replication_count(self) -> int:
        """Return the number of queued replica updates."""
        return len(self._pending_replication)

    def read_task(self, key: TaskKey, *, replica_offset: int = 0) -> ReadResult:
        """Read from a deterministic quorum slice of the owners."""
        owners = self.ring.owners_for_key(key, self.quorum.replica_count)
        start = replica_offset % len(owners)
        rotated = owners[start:] + owners[:start]
        contacted = rotated[: self.quorum.read_quorum]
        visible: dict[str, TaskVersion] = {}
        for node_id in contacted:
            for version in self._nodes[node_id].versions_for(key):
                visible.setdefault(version.version_id, version)
        heads = _head_versions(tuple(visible.values()))
        if not heads:
            raise TaskNotFoundError(key.ring_key())
        return ReadResult(owners=owners, contacted=contacted, versions=heads)

    def read_all_replicas(self, key: TaskKey) -> tuple[TaskVersion, ...]:
        """Read the current head versions across every replica owner."""
        owners = self.ring.owners_for_key(key, self.quorum.replica_count)
        visible: dict[str, TaskVersion] = {}
        for node_id in owners:
            for version in self._nodes[node_id].versions_for(key):
                visible.setdefault(version.version_id, version)
        return _head_versions(tuple(visible.values()))

    def _known_version_ids(self, key: TaskKey) -> set[str]:
        owners = self.ring.owners_for_key(key, self.quorum.replica_count)
        known: set[str] = set()
        for node_id in owners:
            known.update(version.version_id for version in self._nodes[node_id].versions_for(key))
        return known


def _head_versions(versions: tuple[TaskVersion, ...]) -> tuple[TaskVersion, ...]:
    visible = {version.version_id: version for version in versions}
    parent_ids = {
        parent_id
        for version in visible.values()
        for parent_id in version.parents
        if parent_id in visible
    }
    heads = [version for version in visible.values() if version.version_id not in parent_ids]
    return tuple(sorted(heads, key=lambda version: version.version_id))
