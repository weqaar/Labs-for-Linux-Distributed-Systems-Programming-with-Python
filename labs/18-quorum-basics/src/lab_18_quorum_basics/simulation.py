"""Deterministic quorum register simulation for relay task placement."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

NodeId = str
TaskKey = str


class RelayTaskStatus(str, Enum):
    """States a relay task can hold inside the register."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"


@dataclass(frozen=True)
class RelayTaskRecord:
    """Single relay task value stored in the quorum register."""

    task_id: str
    shard: str
    status: RelayTaskStatus
    assigned_worker: str | None = None
    attempt: int = 0


@dataclass(frozen=True, order=True)
class Version:
    """Monotonic version attached to every write."""

    tick: int
    coordinator: NodeId


@dataclass(frozen=True)
class VersionedTask:
    """Task value plus the version used to compare replicas."""

    version: Version
    record: RelayTaskRecord


class QuorumUnavailable(RuntimeError):
    """Raised when a coordinator cannot reach enough replicas."""


class UnknownTask(RuntimeError):
    """Raised when a task has never been written to any contacted replica."""


@dataclass
class SimulationClock:
    """Logical clock the tests advance through writes."""

    tick: int = 0

    def advance(self, steps: int = 1) -> int:
        if steps < 1:
            raise ValueError("steps must be positive")
        self.tick += steps
        return self.tick


@dataclass(frozen=True)
class PartitionMap:
    """Connectivity groups for the simulated network."""

    groups: tuple[frozenset[NodeId], ...]

    @classmethod
    def healed(cls, nodes: Iterable[NodeId]) -> PartitionMap:
        return cls((frozenset(nodes),))

    @classmethod
    def from_groups(
        cls,
        nodes: Iterable[NodeId],
        groups: Sequence[Iterable[NodeId]],
    ) -> PartitionMap:
        known_nodes = set(nodes)
        frozen_groups = tuple(frozenset(group) for group in groups)
        seen: set[NodeId] = set()
        for group in frozen_groups:
            if not group:
                raise ValueError("partition groups must not be empty")
            unknown = group - known_nodes
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(f"unknown nodes in partition: {names}")
            overlap = seen & group
            if overlap:
                names = ", ".join(sorted(overlap))
                raise ValueError(f"nodes may appear in one partition only: {names}")
            seen.update(group)
        if seen != known_nodes:
            missing = ", ".join(sorted(known_nodes - seen))
            raise ValueError(f"partition must cover every node: {missing}")
        return cls(frozen_groups)

    def reachable_from(self, node_id: NodeId) -> tuple[NodeId, ...]:
        for group in self.groups:
            if node_id in group:
                return tuple(sorted(group))
        raise ValueError(f"unknown node {node_id}")


@dataclass
class RegisterNode:
    """Single replica in the quorum register."""

    node_id: NodeId
    store: dict[TaskKey, VersionedTask] = field(default_factory=dict)

    def read(self, task_key: TaskKey) -> VersionedTask | None:
        return self.store.get(task_key)

    def write(self, task_key: TaskKey, value: VersionedTask) -> None:
        current = self.store.get(task_key)
        if current is None or current.version <= value.version:
            self.store[task_key] = value


@dataclass(frozen=True)
class ReadObservation:
    """Replica response collected during a quorum read."""

    node_id: NodeId
    value: VersionedTask | None


@dataclass(frozen=True)
class ReadResult:
    """Outcome of a quorum read."""

    task_key: TaskKey
    value: VersionedTask
    contacted_nodes: tuple[NodeId, ...]
    observations: tuple[ReadObservation, ...]
    stale: bool


def majority_quorum(node_count: int) -> int:
    """Return the majority size for *node_count* replicas."""

    if node_count < 1:
        raise ValueError("node_count must be positive")
    return (node_count // 2) + 1


def majority_failure_budget(node_count: int) -> int:
    """Return how many failures a majority quorum tolerates."""

    return node_count - majority_quorum(node_count)


def quorums_intersect(node_count: int, read_quorum: int, write_quorum: int) -> bool:
    """Return whether every read quorum must overlap every write quorum."""

    if min(node_count, read_quorum, write_quorum) < 1:
        raise ValueError("node_count, read_quorum and write_quorum must be positive")
    if max(read_quorum, write_quorum) > node_count:
        raise ValueError("quorums cannot exceed node_count")
    return read_quorum + write_quorum > node_count


class QuorumRegisterCluster:
    """Relay task register with deterministic quorum reads and writes."""

    def __init__(
        self,
        node_ids: Sequence[NodeId],
        clock: SimulationClock | None = None,
    ) -> None:
        unique_nodes = tuple(dict.fromkeys(node_ids))
        if len(unique_nodes) < 1:
            raise ValueError("at least one node is required")
        self._clock = clock or SimulationClock()
        self._nodes = {node_id: RegisterNode(node_id=node_id) for node_id in unique_nodes}
        self._partition = PartitionMap.healed(unique_nodes)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def node_ids(self) -> tuple[NodeId, ...]:
        return tuple(self._nodes)

    def set_partition(self, groups: Sequence[Sequence[NodeId]]) -> None:
        self._partition = PartitionMap.from_groups(self._nodes, groups)

    def heal(self) -> None:
        self._partition = PartitionMap.healed(self._nodes)

    def write_task(
        self,
        task_key: TaskKey,
        record: RelayTaskRecord,
        coordinator: NodeId,
        write_quorum: int,
        preferred_nodes: Sequence[NodeId] | None = None,
    ) -> VersionedTask:
        targets = self._select_nodes(
            coordinator=coordinator,
            required=write_quorum,
            preferred_nodes=preferred_nodes,
        )
        versioned = VersionedTask(
            version=Version(self._clock.advance(), coordinator),
            record=record,
        )
        for node_id in targets:
            self._nodes[node_id].write(task_key, versioned)
        return versioned

    def read_task(
        self,
        task_key: TaskKey,
        coordinator: NodeId,
        read_quorum: int,
        preferred_nodes: Sequence[NodeId] | None = None,
    ) -> ReadResult:
        targets = self._select_nodes(
            coordinator=coordinator,
            required=read_quorum,
            preferred_nodes=preferred_nodes,
        )
        observations = tuple(
            ReadObservation(node_id=node_id, value=self._nodes[node_id].read(task_key))
            for node_id in targets
        )
        candidates = [
            observation.value for observation in observations if observation.value is not None
        ]
        if not candidates:
            raise UnknownTask(f"{task_key} has not been written")
        latest_seen = max(candidates, key=lambda value: value.version)
        cluster_latest = self.latest_task(task_key)
        return ReadResult(
            task_key=task_key,
            value=latest_seen,
            contacted_nodes=targets,
            observations=observations,
            stale=cluster_latest is not None and latest_seen.version < cluster_latest.version,
        )

    def latest_task(self, task_key: TaskKey) -> VersionedTask | None:
        candidates: list[VersionedTask] = []
        for node in self._nodes.values():
            value = node.read(task_key)
            if value is not None:
                candidates.append(value)
        if not candidates:
            return None
        return max(candidates, key=lambda value: value.version)

    def can_reach_quorum(self, coordinator: NodeId, quorum_size: int) -> bool:
        if coordinator not in self._nodes:
            raise ValueError(f"unknown node {coordinator}")
        if quorum_size < 1 or quorum_size > self.node_count:
            raise ValueError("quorum_size must be between 1 and node_count")
        return len(self._partition.reachable_from(coordinator)) >= quorum_size

    def replica_value(self, node_id: NodeId, task_key: TaskKey) -> VersionedTask | None:
        if node_id not in self._nodes:
            raise ValueError(f"unknown node {node_id}")
        return self._nodes[node_id].read(task_key)

    def _select_nodes(
        self,
        coordinator: NodeId,
        required: int,
        preferred_nodes: Sequence[NodeId] | None,
    ) -> tuple[NodeId, ...]:
        if coordinator not in self._nodes:
            raise ValueError(f"unknown node {coordinator}")
        if required < 1 or required > self.node_count:
            raise ValueError("quorum size must be between 1 and node_count")
        reachable_nodes = self._partition.reachable_from(coordinator)
        if len(reachable_nodes) < required:
            raise QuorumUnavailable(
                f"{coordinator} can reach {len(reachable_nodes)} replicas, needs {required}"
            )
        ordered_reachable = tuple(reachable_nodes)
        if preferred_nodes is None:
            return ordered_reachable[:required]
        unique_preferred = tuple(dict.fromkeys(preferred_nodes))
        if len(unique_preferred) != required:
            raise ValueError("preferred_nodes must name exactly the requested quorum")
        unknown = set(unique_preferred) - set(ordered_reachable)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise QuorumUnavailable(f"{coordinator} cannot reach {names}")
        return unique_preferred


__all__ = [
    "NodeId",
    "PartitionMap",
    "QuorumRegisterCluster",
    "QuorumUnavailable",
    "ReadObservation",
    "ReadResult",
    "RelayTaskRecord",
    "RelayTaskStatus",
    "RegisterNode",
    "SimulationClock",
    "TaskKey",
    "UnknownTask",
    "Version",
    "VersionedTask",
    "majority_failure_budget",
    "majority_quorum",
    "quorums_intersect",
]
