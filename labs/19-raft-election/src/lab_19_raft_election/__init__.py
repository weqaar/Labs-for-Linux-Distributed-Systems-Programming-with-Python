"""Deterministic Raft leader-election simulation for relay coordinators."""

from __future__ import annotations

from .simulation import (
    DeterministicRaftCluster,
    ElectionResult,
    Heartbeat,
    HeartbeatResponse,
    LeadershipConflict,
    LogPosition,
    NodeId,
    NodeRole,
    PartitionMap,
    PersistentVoteState,
    RaftNode,
    RequestVote,
    VoteResponse,
)

__version__ = "0.1.0"

__all__ = [
    "DeterministicRaftCluster",
    "ElectionResult",
    "Heartbeat",
    "HeartbeatResponse",
    "LeadershipConflict",
    "LogPosition",
    "NodeId",
    "NodeRole",
    "PartitionMap",
    "PersistentVoteState",
    "RaftNode",
    "RequestVote",
    "VoteResponse",
    "__version__",
]
