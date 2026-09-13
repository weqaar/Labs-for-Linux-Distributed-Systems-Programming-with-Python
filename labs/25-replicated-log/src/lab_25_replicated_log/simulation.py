"""Deterministic replicated relay log and state-machine simulation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

NodeId = str


class NodeRole(str, Enum):
    """Roles defined by Raft."""

    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


class RelayTaskStatus(str, Enum):
    """Task states the replicated state machine can hold."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"


@dataclass
class SimulationClock:
    """Logical clock for deterministic command payloads."""

    tick: int = 0

    def advance(self, steps: int = 1) -> int:
        if steps < 1:
            raise ValueError("steps must be positive")
        self.tick += steps
        return self.tick


@dataclass(frozen=True)
class RelayTask:
    """Replicated relay task record."""

    task_id: str
    queue: str
    status: RelayTaskStatus
    created_tick: int
    worker_id: str | None = None
    attempt: int = 0
    completed_tick: int | None = None


@dataclass(frozen=True)
class EnqueueTask:
    """Create a new relay task."""

    task_id: str
    queue: str
    created_tick: int


@dataclass(frozen=True)
class StartTask:
    """Claim a queued relay task."""

    task_id: str
    worker_id: str
    attempt: int


@dataclass(frozen=True)
class CompleteTask:
    """Mark a relay task complete."""

    task_id: str
    worker_id: str
    completed_tick: int


RelayCommand = EnqueueTask | StartTask | CompleteTask


class StateMachineError(RuntimeError):
    """Raised when a command would violate task-state rules."""


@dataclass(frozen=True)
class LogEntry:
    """Persistent replicated log entry."""

    index: int
    term: int
    command: RelayCommand


@dataclass(frozen=True, order=True)
class LogPosition:
    """Last replicated log position used for elections."""

    term: int = 0
    index: int = 0


@dataclass
class PersistentNodeState:
    """Durable state that survives crashes and restarts."""

    current_term: int = 0
    voted_for: NodeId | None = None
    log: list[LogEntry] = field(default_factory=list)
    commit_index: int = 0


@dataclass(frozen=True)
class RequestVote:
    """Vote request sent during elections."""

    term: int
    candidate_id: NodeId
    last_log_position: LogPosition


@dataclass(frozen=True)
class VoteResponse:
    """Vote reply from a follower."""

    term: int
    voter_id: NodeId
    granted: bool


@dataclass(frozen=True)
class AppendEntriesRequest:
    """Replication request carrying the leader's authoritative log."""

    term: int
    leader_id: NodeId
    log_entries: tuple[LogEntry, ...]
    leader_commit: int


@dataclass(frozen=True)
class AppendEntriesResponse:
    """Follower reply to an AppendEntries request."""

    term: int
    follower_id: NodeId
    success: bool
    match_index: int


@dataclass(frozen=True)
class CommandResult:
    """Result of submitting one relay command to the leader."""

    leader_id: NodeId
    index: int
    term: int
    committed: bool
    replicated_to: tuple[NodeId, ...]


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


class RelayTaskStateMachine:
    """Deterministic state machine applied from the committed log."""

    def __init__(self) -> None:
        self._tasks: dict[str, RelayTask] = {}
        self._history: list[RelayCommand] = []

    def apply(self, command: RelayCommand) -> None:
        if isinstance(command, EnqueueTask):
            if command.task_id in self._tasks:
                raise StateMachineError(f"{command.task_id} already exists")
            self._tasks[command.task_id] = RelayTask(
                task_id=command.task_id,
                queue=command.queue,
                status=RelayTaskStatus.QUEUED,
                created_tick=command.created_tick,
            )
        elif isinstance(command, StartTask):
            task = self._require_task(command.task_id)
            if task.status is not RelayTaskStatus.QUEUED:
                raise StateMachineError(f"{command.task_id} is not queued")
            self._tasks[command.task_id] = RelayTask(
                task_id=task.task_id,
                queue=task.queue,
                status=RelayTaskStatus.RUNNING,
                created_tick=task.created_tick,
                worker_id=command.worker_id,
                attempt=command.attempt,
            )
        else:
            task = self._require_task(command.task_id)
            if task.status is not RelayTaskStatus.RUNNING:
                raise StateMachineError(f"{command.task_id} is not running")
            if task.worker_id != command.worker_id:
                raise StateMachineError(f"{command.task_id} is owned by {task.worker_id}")
            self._tasks[command.task_id] = RelayTask(
                task_id=task.task_id,
                queue=task.queue,
                status=RelayTaskStatus.COMPLETED,
                created_tick=task.created_tick,
                worker_id=task.worker_id,
                attempt=task.attempt,
                completed_tick=command.completed_tick,
            )
        self._history.append(command)

    def task(self, task_id: str) -> RelayTask | None:
        return self._tasks.get(task_id)

    def snapshot(self) -> dict[str, RelayTask]:
        return dict(self._tasks)

    def history(self) -> tuple[RelayCommand, ...]:
        return tuple(self._history)

    def _require_task(self, task_id: str) -> RelayTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise StateMachineError(f"{task_id} does not exist")
        return task


@dataclass
class ReplicatedLogNode:
    """Single node in the replicated relay cluster."""

    node_id: NodeId
    peers: tuple[NodeId, ...]
    persistent_state: PersistentNodeState = field(default_factory=PersistentNodeState)
    role: NodeRole = NodeRole.FOLLOWER
    leader_id: NodeId | None = None
    running: bool = True
    state_machine: RelayTaskStateMachine = field(init=False)
    last_applied: int = field(init=False, default=0)
    match_index_by_peer: dict[NodeId, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.state_machine = RelayTaskStateMachine()
        self.rebuild_state_machine()

    @property
    def current_term(self) -> int:
        return self.persistent_state.current_term

    @property
    def voted_for(self) -> NodeId | None:
        return self.persistent_state.voted_for

    @property
    def log(self) -> tuple[LogEntry, ...]:
        return tuple(self.persistent_state.log)

    @property
    def commit_index(self) -> int:
        return self.persistent_state.commit_index

    @property
    def last_log_position(self) -> LogPosition:
        if not self.persistent_state.log:
            return LogPosition()
        last_entry = self.persistent_state.log[-1]
        return LogPosition(term=last_entry.term, index=last_entry.index)

    def restart(self) -> None:
        self.running = True
        self.role = NodeRole.FOLLOWER
        self.leader_id = None
        self.match_index_by_peer = {}
        self.rebuild_state_machine()

    def crash(self) -> None:
        self.running = False
        self.role = NodeRole.FOLLOWER
        self.leader_id = None
        self.match_index_by_peer = {}

    def observe_higher_term(self, term: int) -> None:
        if term <= self.current_term:
            return
        self.persistent_state.current_term = term
        self.persistent_state.voted_for = None
        self.role = NodeRole.FOLLOWER
        self.leader_id = None
        self.match_index_by_peer = {}

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

    def receive_append_entries(self, request: AppendEntriesRequest) -> AppendEntriesResponse:
        if request.term < self.current_term:
            return AppendEntriesResponse(
                term=self.current_term,
                follower_id=self.node_id,
                success=False,
                match_index=self.last_log_position.index,
            )
        if request.term > self.current_term:
            self.observe_higher_term(request.term)
        self.role = NodeRole.FOLLOWER
        self.leader_id = request.leader_id
        self._replace_log(tuple(request.log_entries))
        self.persistent_state.commit_index = min(
            request.leader_commit, len(self.persistent_state.log)
        )
        self.apply_committed_entries()
        return AppendEntriesResponse(
            term=self.current_term,
            follower_id=self.node_id,
            success=True,
            match_index=self.last_log_position.index,
        )

    def become_leader(self) -> None:
        self.role = NodeRole.LEADER
        self.leader_id = self.node_id
        self.match_index_by_peer = {self.node_id: self.last_log_position.index}
        for peer_id in self.peers:
            self.match_index_by_peer.setdefault(peer_id, 0)

    def append_local_entry(self, command: RelayCommand) -> LogEntry:
        entry = LogEntry(
            index=len(self.persistent_state.log) + 1,
            term=self.current_term,
            command=command,
        )
        self.persistent_state.log.append(entry)
        self.match_index_by_peer[self.node_id] = entry.index
        return entry

    def apply_committed_entries(self) -> None:
        while self.last_applied < self.commit_index:
            entry = self.persistent_state.log[self.last_applied]
            self.state_machine.apply(entry.command)
            self.last_applied += 1

    def rebuild_state_machine(self) -> None:
        self.state_machine = RelayTaskStateMachine()
        self.last_applied = 0
        self.persistent_state.commit_index = min(
            self.persistent_state.commit_index,
            len(self.persistent_state.log),
        )
        self.apply_committed_entries()

    def _replace_log(self, authoritative_log: tuple[LogEntry, ...]) -> None:
        shared_prefix = 0
        for leader_entry, local_entry in zip(authoritative_log, self.persistent_state.log):
            if leader_entry == local_entry:
                shared_prefix += 1
                continue
            break
        del self.persistent_state.log[shared_prefix:]
        self.persistent_state.log.extend(authoritative_log[shared_prefix:])
        if self.persistent_state.commit_index > len(self.persistent_state.log):
            self.persistent_state.commit_index = len(self.persistent_state.log)
        if self.last_applied > self.persistent_state.commit_index:
            self.rebuild_state_machine()


class LeadershipConflict(RuntimeError):
    """Raised if two leaders would be recorded for the same term."""


class ReplicatedRelayCluster:
    """Deterministic relay cluster with Raft-style replication semantics."""

    def __init__(self, node_ids: Sequence[NodeId]) -> None:
        unique_nodes = tuple(dict.fromkeys(node_ids))
        if len(unique_nodes) < 3:
            raise ValueError("at least three nodes are required")
        self._partition = PartitionMap.healed(unique_nodes)
        self._leader_by_term: dict[int, NodeId] = {}
        self._nodes = {
            node_id: ReplicatedLogNode(
                node_id=node_id,
                peers=tuple(peer for peer in unique_nodes if peer != node_id),
            )
            for node_id in unique_nodes
        }

    @property
    def majority(self) -> int:
        return (len(self._nodes) // 2) + 1

    def node(self, node_id: NodeId) -> ReplicatedLogNode:
        try:
            return self._nodes[node_id]
        except KeyError as error:
            raise ValueError(f"unknown node {node_id}") from error

    def set_partition(self, groups: Sequence[Sequence[NodeId]]) -> None:
        self._partition = PartitionMap.from_groups(self._nodes, groups)

    def heal(self) -> None:
        self._partition = PartitionMap.healed(self._nodes)

    def crash_node(self, node_id: NodeId) -> None:
        self.node(node_id).crash()

    def restart_node(self, node_id: NodeId) -> ReplicatedLogNode:
        node = self.node(node_id)
        node.restart()
        return node

    def trigger_timeout(self, node_id: NodeId) -> NodeRole:
        node = self._require_running_node(node_id)
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
        return node.role

    def submit_command(self, leader_id: NodeId, command: RelayCommand) -> CommandResult:
        leader = self._require_running_node(leader_id)
        if leader.role is not NodeRole.LEADER:
            raise ValueError(f"{leader_id} is not the current leader")
        entry = leader.append_local_entry(command)
        replicated_to = {leader_id}
        for response in self.replicate_from_leader(leader_id):
            if response.success:
                replicated_to.add(response.follower_id)
        committed = self._advance_commit_index(leader)
        if committed:
            self.replicate_from_leader(leader_id)
        return CommandResult(
            leader_id=leader_id,
            index=entry.index,
            term=entry.term,
            committed=entry.index <= leader.commit_index,
            replicated_to=tuple(sorted(replicated_to)),
        )

    def replicate_from_leader(self, leader_id: NodeId) -> tuple[AppendEntriesResponse, ...]:
        leader = self._require_running_node(leader_id)
        if leader.role is not NodeRole.LEADER:
            raise ValueError(f"{leader_id} is not the current leader")
        request = AppendEntriesRequest(
            term=leader.current_term,
            leader_id=leader_id,
            log_entries=leader.log,
            leader_commit=leader.commit_index,
        )
        responses: list[AppendEntriesResponse] = []
        for peer_id in self._reachable_peers(leader_id):
            follower = self.node(peer_id)
            response = follower.receive_append_entries(request)
            responses.append(response)
            if response.term > leader.current_term:
                leader.observe_higher_term(response.term)
                continue
            leader.match_index_by_peer[peer_id] = response.match_index
        leader.match_index_by_peer[leader_id] = leader.last_log_position.index
        return tuple(responses)

    def _advance_commit_index(self, leader: ReplicatedLogNode) -> bool:
        new_commit_index = leader.commit_index
        for candidate_index in range(leader.commit_index + 1, leader.last_log_position.index + 1):
            entry = leader.log[candidate_index - 1]
            if entry.term != leader.current_term:
                continue
            replicated_nodes = sum(
                1
                for match_index in leader.match_index_by_peer.values()
                if match_index >= candidate_index
            )
            if replicated_nodes >= self.majority:
                new_commit_index = candidate_index
        if new_commit_index == leader.commit_index:
            return False
        leader.persistent_state.commit_index = new_commit_index
        leader.apply_committed_entries()
        return True

    def _reachable_peers(self, node_id: NodeId) -> tuple[NodeId, ...]:
        return tuple(
            peer_id
            for peer_id in self._partition.reachable_from(node_id)
            if peer_id != node_id and self.node(peer_id).running
        )

    def _require_running_node(self, node_id: NodeId) -> ReplicatedLogNode:
        node = self.node(node_id)
        if not node.running:
            raise ValueError(f"{node_id} is crashed")
        return node

    def _record_leader(self, node: ReplicatedLogNode) -> None:
        existing = self._leader_by_term.get(node.current_term)
        if existing is not None and existing != node.node_id:
            raise LeadershipConflict(
                f"term {node.current_term} already led by {existing}, not {node.node_id}"
            )
        self._leader_by_term[node.current_term] = node.node_id


__all__ = [
    "AppendEntriesRequest",
    "AppendEntriesResponse",
    "CommandResult",
    "CompleteTask",
    "EnqueueTask",
    "LeadershipConflict",
    "LogEntry",
    "LogPosition",
    "NodeId",
    "NodeRole",
    "PartitionMap",
    "PersistentNodeState",
    "RelayCommand",
    "RelayTask",
    "RelayTaskStateMachine",
    "RelayTaskStatus",
    "ReplicatedLogNode",
    "ReplicatedRelayCluster",
    "RequestVote",
    "SimulationClock",
    "StartTask",
    "StateMachineError",
    "VoteResponse",
]
