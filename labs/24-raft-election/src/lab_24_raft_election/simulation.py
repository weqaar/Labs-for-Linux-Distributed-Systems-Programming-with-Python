"""Deterministic Raft leader-election simulation for relay coordinators."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

NodeId = str


class NodeRole(str, Enum):
    """Roles defined by the Raft election protocol."""

    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass(frozen=True, order=True)
class LogPosition:
    """Last replicated log position used during elections."""

    term: int = 0
    index: int = 0


@dataclass
class PersistentVoteState:
    """Durable state that survives a node restart."""

    current_term: int = 0
    voted_for: NodeId | None = None
    last_log_position: LogPosition = field(default_factory=LogPosition)


@dataclass(frozen=True)
class RequestVote:
    """Vote request sent by a candidate."""

    term: int
    candidate_id: NodeId
    last_log_position: LogPosition = LogPosition()


@dataclass(frozen=True)
class VoteResponse:
    """Vote reply from a follower."""

    term: int
    voter_id: NodeId
    granted: bool


@dataclass(frozen=True)
class Heartbeat:
    """AppendEntries heartbeat used to assert leadership."""

    term: int
    leader_id: NodeId


@dataclass(frozen=True)
class HeartbeatResponse:
    """Follower reply to a heartbeat."""

    term: int
    follower_id: NodeId
    accepted: bool


@dataclass(frozen=True)
class ElectionResult:
    """Deterministic outcome of a simulated timeout."""

    candidate_id: NodeId
    term: int
    granted_by: tuple[NodeId, ...]
    role_after: NodeRole

    @property
    def became_leader(self) -> bool:
        return self.role_after is NodeRole.LEADER


@dataclass(frozen=True)
class PartitionMap:
    """Connectivity groups for the scripted network."""

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


class LeadershipConflict(RuntimeError):
    """Raised if two leaders would be recorded for the same term."""


@dataclass
class RaftNode:
    """Single deterministic Raft node."""

    node_id: NodeId
    peers: tuple[NodeId, ...]
    persistent_state: PersistentVoteState = field(default_factory=PersistentVoteState)
    role: NodeRole = NodeRole.FOLLOWER
    leader_id: NodeId | None = None

    @property
    def current_term(self) -> int:
        return self.persistent_state.current_term

    @property
    def voted_for(self) -> NodeId | None:
        return self.persistent_state.voted_for

    @property
    def last_log_position(self) -> LogPosition:
        return self.persistent_state.last_log_position

    def restart(self) -> None:
        self.role = NodeRole.FOLLOWER
        self.leader_id = None

    def observe_higher_term(self, term: int) -> None:
        if term <= self.current_term:
            return
        self.persistent_state.current_term = term
        self.persistent_state.voted_for = None
        self.role = NodeRole.FOLLOWER
        self.leader_id = None

    def start_election(self) -> RequestVote:
        self.persistent_state.current_term += 1
        self.persistent_state.voted_for = self.node_id
        self.role = NodeRole.CANDIDATE
        self.leader_id = None
        return RequestVote(
            term=self.current_term,
            candidate_id=self.node_id,
            last_log_position=self.last_log_position,
        )

    def receive_vote_request(self, request: RequestVote) -> VoteResponse:
        if request.term < self.current_term:
            return VoteResponse(term=self.current_term, voter_id=self.node_id, granted=False)
        if request.term > self.current_term:
            self.observe_higher_term(request.term)
        can_vote = self.voted_for in (None, request.candidate_id)
        up_to_date = request.last_log_position >= self.last_log_position
        granted = can_vote and up_to_date
        if granted:
            self.persistent_state.voted_for = request.candidate_id
            self.role = NodeRole.FOLLOWER
            self.leader_id = None
        return VoteResponse(term=self.current_term, voter_id=self.node_id, granted=granted)

    def receive_heartbeat(self, heartbeat: Heartbeat) -> HeartbeatResponse:
        if heartbeat.term < self.current_term:
            return HeartbeatResponse(
                term=self.current_term, follower_id=self.node_id, accepted=False
            )
        if heartbeat.term > self.current_term:
            self.observe_higher_term(heartbeat.term)
        self.role = NodeRole.FOLLOWER
        self.leader_id = heartbeat.leader_id
        return HeartbeatResponse(term=self.current_term, follower_id=self.node_id, accepted=True)

    def become_leader(self) -> None:
        self.role = NodeRole.LEADER
        self.leader_id = self.node_id


class DeterministicRaftCluster:
    """Election-only Raft cluster with test-controlled timeouts."""

    def __init__(self, node_ids: Sequence[NodeId]) -> None:
        unique_nodes = tuple(dict.fromkeys(node_ids))
        if len(unique_nodes) < 3:
            raise ValueError("Raft needs at least three nodes")
        self._partition = PartitionMap.healed(unique_nodes)
        self._leader_by_term: dict[int, NodeId] = {}
        self._nodes = {
            node_id: RaftNode(
                node_id=node_id,
                peers=tuple(peer for peer in unique_nodes if peer != node_id),
            )
            for node_id in unique_nodes
        }

    @property
    def majority(self) -> int:
        return (len(self._nodes) // 2) + 1

    @property
    def node_ids(self) -> tuple[NodeId, ...]:
        return tuple(self._nodes)

    def node(self, node_id: NodeId) -> RaftNode:
        try:
            return self._nodes[node_id]
        except KeyError as error:
            raise ValueError(f"unknown node {node_id}") from error

    def set_partition(self, groups: Sequence[Sequence[NodeId]]) -> None:
        self._partition = PartitionMap.from_groups(self._nodes, groups)

    def heal(self) -> None:
        self._partition = PartitionMap.healed(self._nodes)

    def restart_node(self, node_id: NodeId) -> RaftNode:
        node = self.node(node_id)
        node.restart()
        return node

    def leader_for_term(self, term: int) -> NodeId | None:
        return self._leader_by_term.get(term)

    def trigger_timeout(self, node_id: NodeId) -> ElectionResult:
        node = self.node(node_id)
        request = node.start_election()
        granted_by: set[NodeId] = {node_id}
        for peer_id in self._reachable_peers(node_id):
            response = self.node(peer_id).receive_vote_request(request)
            if response.term > node.current_term:
                node.observe_higher_term(response.term)
                break
            if response.granted:
                granted_by.add(peer_id)
        if len(granted_by) >= self.majority:
            node.become_leader()
            self._record_leader(node)
        return ElectionResult(
            candidate_id=node_id,
            term=node.current_term,
            granted_by=tuple(sorted(granted_by)),
            role_after=node.role,
        )

    def send_heartbeat(self, leader_id: NodeId) -> tuple[HeartbeatResponse, ...]:
        leader = self.node(leader_id)
        if leader.role is not NodeRole.LEADER:
            raise ValueError(f"{leader_id} is not the current leader")
        heartbeat = Heartbeat(term=leader.current_term, leader_id=leader_id)
        responses: list[HeartbeatResponse] = []
        for peer_id in self._reachable_peers(leader_id):
            response = self.node(peer_id).receive_heartbeat(heartbeat)
            responses.append(response)
            if response.term > leader.current_term:
                leader.observe_higher_term(response.term)
        return tuple(responses)

    def _reachable_peers(self, node_id: NodeId) -> tuple[NodeId, ...]:
        return tuple(
            peer_id for peer_id in self._partition.reachable_from(node_id) if peer_id != node_id
        )

    def _record_leader(self, node: RaftNode) -> None:
        existing = self._leader_by_term.get(node.current_term)
        if existing is not None and existing != node.node_id:
            raise LeadershipConflict(
                f"term {node.current_term} already led by {existing}, not {node.node_id}"
            )
        self._leader_by_term[node.current_term] = node.node_id


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
]
