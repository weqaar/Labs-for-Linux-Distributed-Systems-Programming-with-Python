"""Small deterministic resource scheduler for SigRaft jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum


class JobState(str, Enum):
    """Lifecycle states persisted for one SigRaft job."""

    QUEUED = "queued"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnschedulableRequestError(ValueError):
    """Raised when no registered node can ever satisfy a request."""


class LeadershipError(RuntimeError):
    """Raised when a non-leader attempts to make a placement decision."""


class InvalidTransitionError(RuntimeError):
    """Raised when a job lifecycle transition is not permitted."""


class ConcurrentReservationError(RuntimeError):
    """Raised when replicated state changed before a reservation committed."""


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    """Resources and topology constraints requested by one job."""

    cpu_cores: int
    memory_mb: int
    gpu_count: int = 0
    gpu_class: str | None = None
    wall_time_seconds: int = 3600
    numa_node: int | None = None
    cpu_affinity: tuple[int, ...] = ()
    required_labels: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.cpu_cores < 1:
            raise ValueError("cpu_cores must be positive")
        if self.memory_mb < 1:
            raise ValueError("memory_mb must be positive")
        if self.gpu_count < 0:
            raise ValueError("gpu_count must not be negative")
        if self.gpu_class is not None and self.gpu_count == 0:
            raise ValueError("gpu_class requires at least one GPU")
        if self.wall_time_seconds < 1:
            raise ValueError("wall_time_seconds must be positive")
        if self.numa_node is not None and self.numa_node < 0:
            raise ValueError("numa_node must not be negative")
        if len(set(self.cpu_affinity)) != len(self.cpu_affinity):
            raise ValueError("cpu_affinity entries must be unique")
        if any(cpu < 0 for cpu in self.cpu_affinity):
            raise ValueError("cpu_affinity entries must not be negative")
        if self.cpu_affinity and len(self.cpu_affinity) < self.cpu_cores:
            raise ValueError("cpu_affinity must contain at least cpu_cores entries")


@dataclass(frozen=True, slots=True)
class GpuDevice:
    """One schedulable GPU and its topology."""

    device_id: str
    device_class: str
    numa_node: int | None


@dataclass(frozen=True, slots=True)
class NodeInventory:
    """Resources reported by one execution node heartbeat."""

    node_id: str
    cpu_ids: tuple[int, ...]
    memory_mb: int
    gpus: tuple[GpuDevice, ...] = ()
    numa_cpus: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    labels: frozenset[str] = frozenset()
    heartbeat_at: int = 0

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("node_id must not be empty")
        if not self.cpu_ids or len(set(self.cpu_ids)) != len(self.cpu_ids):
            raise ValueError("cpu_ids must be non-empty and unique")
        if self.memory_mb < 1:
            raise ValueError("memory_mb must be positive")
        known = set(self.cpu_ids)
        if any(cpu not in known for cpus in self.numa_cpus.values() for cpu in cpus):
            raise ValueError("NUMA CPU sets must be subsets of cpu_ids")
        gpu_ids = [gpu.device_id for gpu in self.gpus]
        if len(set(gpu_ids)) != len(gpu_ids):
            raise ValueError("GPU device ids must be unique")


@dataclass(frozen=True, slots=True)
class ProjectQuota:
    """Maximum active allocations for one project."""

    jobs: int
    cpu_cores: int
    memory_mb: int
    gpu_count: int = 0

    def __post_init__(self) -> None:
        if min(self.jobs, self.cpu_cores, self.memory_mb) < 1 or self.gpu_count < 0:
            raise ValueError("quota values must be positive, except gpu_count may be zero")


@dataclass(frozen=True, slots=True)
class Job:
    """Persisted scheduler state for one submitted task."""

    job_id: str
    project: str
    action: str
    request: ResourceRequest
    priority: int
    submitted_sequence: int
    max_attempts: int
    attempts: int = 0
    state: JobState = JobState.QUEUED
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class Allocation:
    """Atomic reservation recorded before dispatch."""

    allocation_id: str
    job_id: str
    project: str
    node_id: str
    cpu_ids: tuple[int, ...]
    memory_mb: int
    gpu_ids: tuple[str, ...]
    numa_node: int | None
    leader_term: int
    lease_expires_at: int


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    """Node-agent enforcement plan produced from an allocation."""

    allocation_id: str
    job_id: str
    queue: str
    cgroup: str
    cpu_set: str
    memory_max_bytes: int
    numa_node: int | None
    environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One committed scheduler state transition."""

    revision: int
    leader_term: int
    event: str
    job_id: str
    node_id: str | None


@dataclass
class ReplicatedStateLog:
    """Deterministic stand-in for appending through the Raft state machine."""

    entries: list[LogEntry] = field(default_factory=list)

    @property
    def revision(self) -> int:
        return len(self.entries)

    def append(
        self,
        *,
        expected_revision: int,
        leader_term: int,
        event: str,
        job_id: str,
        node_id: str | None = None,
    ) -> LogEntry:
        if expected_revision != self.revision:
            raise ConcurrentReservationError("replicated state revision changed")
        entry = LogEntry(self.revision + 1, leader_term, event, job_id, node_id)
        self.entries.append(entry)
        return entry


@dataclass
class ResourceScheduler:
    """Leader-gated, deterministic scheduler with atomic reservations."""

    heartbeat_timeout: int = 30
    quotas: Mapping[str, ProjectQuota] = field(default_factory=dict)
    log: ReplicatedStateLog = field(default_factory=ReplicatedStateLog)
    _nodes: dict[str, NodeInventory] = field(default_factory=dict)
    _jobs: dict[str, Job] = field(default_factory=dict)
    _allocations: dict[str, Allocation] = field(default_factory=dict)
    _submission_sequence: int = 0
    _leader_id: str | None = None
    _leader_term: int = 0

    def become_leader(self, node_id: str, term: int) -> None:
        """Authorize one scheduler instance for a newer Raft term."""

        if not node_id:
            raise ValueError("leader node_id must not be empty")
        if term <= self._leader_term:
            raise LeadershipError("leader term must increase")
        self._leader_id = node_id
        self._leader_term = term

    def heartbeat(self, inventory: NodeInventory) -> None:
        """Replace one node's inventory with its latest complete report."""

        previous = self._nodes.get(inventory.node_id)
        if previous is not None and inventory.heartbeat_at < previous.heartbeat_at:
            raise ValueError("heartbeat time must not move backwards")
        self._nodes[inventory.node_id] = inventory

    def submit(
        self,
        job_id: str,
        *,
        project: str,
        action: str,
        request: ResourceRequest,
        priority: int = 0,
        max_attempts: int = 1,
    ) -> Job:
        """Validate and persist a queued job."""

        if not _valid_job_id(job_id):
            raise ValueError("job_id must match task-<positive integer>")
        if not project.strip() or not action.strip():
            raise ValueError("project and action must not be empty")
        if not 0 <= priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if job_id in self._jobs:
            raise ValueError("job_id already exists")
        if self._nodes and not any(
            _statically_fits(request, node) for node in self._nodes.values()
        ):
            raise UnschedulableRequestError("no registered node can satisfy the request")
        self._submission_sequence += 1
        job = Job(
            job_id=job_id,
            project=project.strip(),
            action=action.strip(),
            request=request,
            priority=priority,
            submitted_sequence=self._submission_sequence,
            max_attempts=max_attempts,
        )
        self._jobs[job_id] = job
        self.log.append(
            expected_revision=self.log.revision,
            leader_term=self._leader_term,
            event="submitted",
            job_id=job_id,
        )
        return job

    def schedule(
        self,
        *,
        caller_id: str,
        now: int,
        lease_seconds: int = 30,
    ) -> tuple[DispatchPlan, ...]:
        """Place all currently eligible jobs in deterministic order."""

        self._require_leader(caller_id)
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.recover_expired(caller_id=caller_id, now=now)
        plans: list[DispatchPlan] = []
        queued = sorted(
            (job for job in self._jobs.values() if job.state is JobState.QUEUED),
            key=lambda job: (-job.priority, job.submitted_sequence, job.job_id),
        )
        for job in queued:
            if not self._within_quota(job):
                continue
            candidates = [
                node
                for node in self._nodes.values()
                if now - node.heartbeat_at <= self.heartbeat_timeout
                and self._available_fit(job.request, node)
            ]
            if not candidates:
                continue
            node = min(candidates, key=lambda candidate: self._score(job.request, candidate))
            allocation = self._reserve(job, node, now=now, lease_seconds=lease_seconds)
            plans.append(_dispatch_plan(allocation))
        return tuple(plans)

    def mark_running(self, allocation_id: str, *, caller_id: str) -> Job:
        """Record that the selected node started the allocation."""

        self._require_leader(caller_id)
        allocation = self._allocation(allocation_id)
        job = self._job(allocation.job_id)
        if job.state is not JobState.SCHEDULED:
            raise InvalidTransitionError("only scheduled jobs can start")
        return self._transition(job, JobState.RUNNING, "running", allocation.node_id)

    def complete(
        self,
        allocation_id: str,
        *,
        caller_id: str,
        succeeded: bool,
        error: str | None = None,
    ) -> Job:
        """Complete an allocation and release its reserved resources."""

        self._require_leader(caller_id)
        allocation = self._allocation(allocation_id)
        job = self._job(allocation.job_id)
        if job.state not in {JobState.SCHEDULED, JobState.RUNNING}:
            raise InvalidTransitionError("only active jobs can complete")
        state = JobState.SUCCEEDED if succeeded else JobState.FAILED
        updated = self._transition(job, state, state.value, allocation.node_id, error)
        del self._allocations[allocation_id]
        return updated

    def cancel(self, job_id: str, *, caller_id: str) -> Job:
        """Cancel queued or active work and release any reservation."""

        self._require_leader(caller_id)
        job = self._job(job_id)
        if job.state not in {JobState.QUEUED, JobState.SCHEDULED, JobState.RUNNING}:
            raise InvalidTransitionError("job is already terminal")
        allocation = next(
            (item for item in self._allocations.values() if item.job_id == job_id),
            None,
        )
        updated = self._transition(
            job,
            JobState.CANCELLED,
            "cancelled",
            allocation.node_id if allocation else None,
        )
        if allocation is not None:
            del self._allocations[allocation.allocation_id]
        return updated

    def recover_expired(self, *, caller_id: str, now: int) -> tuple[Job, ...]:
        """Requeue or fail allocations whose leases expired."""

        self._require_leader(caller_id)
        recovered: list[Job] = []
        for allocation in sorted(self._allocations.values(), key=lambda item: item.allocation_id):
            if allocation.lease_expires_at > now:
                continue
            job = self._job(allocation.job_id)
            state = JobState.QUEUED if job.attempts < job.max_attempts else JobState.FAILED
            error = "allocation lease expired"
            recovered.append(
                self._transition(job, state, "lease-expired", allocation.node_id, error)
            )
            del self._allocations[allocation.allocation_id]
        return tuple(recovered)

    def job(self, job_id: str) -> Job:
        """Return a persisted job."""

        return self._job(job_id)

    @property
    def allocations(self) -> tuple[Allocation, ...]:
        """Return active allocations ordered by allocation id."""

        return tuple(sorted(self._allocations.values(), key=lambda item: item.allocation_id))

    def _reserve(
        self,
        job: Job,
        node: NodeInventory,
        *,
        now: int,
        lease_seconds: int,
    ) -> Allocation:
        used_cpus, _, used_gpus = self._used(node.node_id)
        eligible_cpus = _eligible_cpus(job.request, node)
        cpu_ids = tuple(cpu for cpu in eligible_cpus if cpu not in used_cpus)[
            : job.request.cpu_cores
        ]
        eligible_gpus = _eligible_gpus(job.request, node)
        gpu_ids = tuple(gpu.device_id for gpu in eligible_gpus if gpu.device_id not in used_gpus)[
            : job.request.gpu_count
        ]
        allocation_id = f"allocation-{self.log.revision + 1}"
        expected_revision = self.log.revision
        allocation = Allocation(
            allocation_id=allocation_id,
            job_id=job.job_id,
            project=job.project,
            node_id=node.node_id,
            cpu_ids=cpu_ids,
            memory_mb=job.request.memory_mb,
            gpu_ids=gpu_ids,
            numa_node=job.request.numa_node,
            leader_term=self._leader_term,
            lease_expires_at=now + lease_seconds,
        )
        self.log.append(
            expected_revision=expected_revision,
            leader_term=self._leader_term,
            event="reserved",
            job_id=job.job_id,
            node_id=node.node_id,
        )
        self._allocations[allocation_id] = allocation
        self._jobs[job.job_id] = replace(
            job,
            state=JobState.SCHEDULED,
            attempts=job.attempts + 1,
            last_error=None,
        )
        return allocation

    def _within_quota(self, job: Job) -> bool:
        quota = self.quotas.get(job.project)
        if quota is None:
            return True
        active = [
            allocation
            for allocation in self._allocations.values()
            if allocation.project == job.project
        ]
        return (
            len(active) + 1 <= quota.jobs
            and sum(len(item.cpu_ids) for item in active) + job.request.cpu_cores <= quota.cpu_cores
            and sum(item.memory_mb for item in active) + job.request.memory_mb <= quota.memory_mb
            and sum(len(item.gpu_ids) for item in active) + job.request.gpu_count <= quota.gpu_count
        )

    def _available_fit(self, request: ResourceRequest, node: NodeInventory) -> bool:
        if not _statically_fits(request, node):
            return False
        used_cpus, used_memory, used_gpus = self._used(node.node_id)
        cpu_count = sum(cpu not in used_cpus for cpu in _eligible_cpus(request, node))
        gpu_count = sum(gpu.device_id not in used_gpus for gpu in _eligible_gpus(request, node))
        return (
            cpu_count >= request.cpu_cores
            and node.memory_mb - used_memory >= request.memory_mb
            and gpu_count >= request.gpu_count
        )

    def _score(self, request: ResourceRequest, node: NodeInventory) -> tuple[int, int, int, str]:
        used_cpus, used_memory, used_gpus = self._used(node.node_id)
        local_gpu_penalty = 0
        if request.numa_node is not None and request.gpu_count:
            matching = sum(
                gpu.device_id not in used_gpus and gpu.numa_node == request.numa_node
                for gpu in _eligible_gpus(request, node)
            )
            local_gpu_penalty = 0 if matching >= request.gpu_count else 1
        return (
            local_gpu_penalty,
            len(node.cpu_ids) - len(used_cpus) - request.cpu_cores,
            node.memory_mb - used_memory - request.memory_mb,
            node.node_id,
        )

    def _used(self, node_id: str) -> tuple[set[int], int, set[str]]:
        active = [
            allocation for allocation in self._allocations.values() if allocation.node_id == node_id
        ]
        return (
            {cpu for allocation in active for cpu in allocation.cpu_ids},
            sum(allocation.memory_mb for allocation in active),
            {gpu for allocation in active for gpu in allocation.gpu_ids},
        )

    def _transition(
        self,
        job: Job,
        state: JobState,
        event: str,
        node_id: str | None,
        error: str | None = None,
    ) -> Job:
        self.log.append(
            expected_revision=self.log.revision,
            leader_term=self._leader_term,
            event=event,
            job_id=job.job_id,
            node_id=node_id,
        )
        updated = replace(job, state=state, last_error=error)
        self._jobs[job.job_id] = updated
        return updated

    def _require_leader(self, caller_id: str) -> None:
        if caller_id != self._leader_id:
            raise LeadershipError("only the elected scheduler may change placement state")

    def _job(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"unknown job {job_id}") from exc

    def _allocation(self, allocation_id: str) -> Allocation:
        try:
            return self._allocations[allocation_id]
        except KeyError as exc:
            raise KeyError(f"unknown allocation {allocation_id}") from exc


def _valid_job_id(job_id: str) -> bool:
    return job_id.startswith("task-") and job_id[5:].isdigit() and not job_id[5:].startswith("0")


def _eligible_cpus(request: ResourceRequest, node: NodeInventory) -> tuple[int, ...]:
    cpus = request.cpu_affinity or node.cpu_ids
    if request.numa_node is not None:
        local = set(node.numa_cpus.get(request.numa_node, ()))
        cpus = tuple(cpu for cpu in cpus if cpu in local)
    return tuple(sorted(cpus))


def _eligible_gpus(request: ResourceRequest, node: NodeInventory) -> tuple[GpuDevice, ...]:
    return tuple(
        gpu
        for gpu in node.gpus
        if request.gpu_class is None or gpu.device_class == request.gpu_class
        if request.numa_node is None or gpu.numa_node == request.numa_node
    )


def _statically_fits(request: ResourceRequest, node: NodeInventory) -> bool:
    return (
        request.required_labels <= node.labels
        and request.memory_mb <= node.memory_mb
        and len(_eligible_cpus(request, node)) >= request.cpu_cores
        and len(_eligible_gpus(request, node)) >= request.gpu_count
    )


def _dispatch_plan(allocation: Allocation) -> DispatchPlan:
    environment = (
        {"CUDA_VISIBLE_DEVICES": ",".join(allocation.gpu_ids)} if allocation.gpu_ids else {}
    )
    return DispatchPlan(
        allocation_id=allocation.allocation_id,
        job_id=allocation.job_id,
        queue=f"sigraft.node.{allocation.node_id}",
        cgroup=f"sigraft/{allocation.job_id}",
        cpu_set=",".join(str(cpu) for cpu in allocation.cpu_ids),
        memory_max_bytes=allocation.memory_mb * 1024 * 1024,
        numa_node=allocation.numa_node,
        environment=environment,
    )
