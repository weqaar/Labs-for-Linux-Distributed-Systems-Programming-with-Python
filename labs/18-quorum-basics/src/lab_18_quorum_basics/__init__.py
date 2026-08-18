"""Deterministic quorum register simulation for relay task placement."""

from __future__ import annotations

from .simulation import (
    NodeId,
    PartitionMap,
    QuorumRegisterCluster,
    QuorumUnavailable,
    ReadObservation,
    ReadResult,
    RegisterNode,
    RelayTaskRecord,
    RelayTaskStatus,
    SimulationClock,
    TaskKey,
    UnknownTask,
    Version,
    VersionedTask,
    majority_failure_budget,
    majority_quorum,
    quorums_intersect,
)

__version__ = "0.1.0"

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
    "__version__",
    "majority_failure_budget",
    "majority_quorum",
    "quorums_intersect",
]
