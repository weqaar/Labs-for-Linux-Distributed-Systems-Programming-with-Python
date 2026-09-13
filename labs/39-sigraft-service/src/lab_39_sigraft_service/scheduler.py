"""Resource-aware placement integrated into the completed SigRaft service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any


class ScheduledState(str, Enum):
    """Lifecycle states owned by the resource scheduler."""

    QUEUED = "queued"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SchedulerResources:
    """Validated compute resources requested by one task."""

    cpu_cores: int
    memory_mb: int
    gpu_count: int = 0
    gpu_class: str | None = None
    wall_time_seconds: int = 3600
    numa_node: int | None = None
    cpu_affinity: tuple[int, ...] = ()
    labels: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.cpu_cores < 1 or self.memory_mb < 1 or self.wall_time_seconds < 1:
            raise ValueError("CPU, memory and wall time must be positive")
        if self.gpu_count < 0:
            raise ValueError("gpu_count must not be negative")
        if self.gpu_class is not None and self.gpu_count == 0:
            raise ValueError("gpu_class requires at least one GPU")
        if self.numa_node is not None and self.numa_node < 0:
            raise ValueError("numa_node must not be negative")
        if len(set(self.cpu_affinity)) != len(self.cpu_affinity):
            raise ValueError("cpu_affinity entries must be unique")
        if self.cpu_affinity and len(self.cpu_affinity) < self.cpu_cores:
            raise ValueError("cpu_affinity is smaller than cpu_cores")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SchedulerResources:
        """Build a request from a bounded HTTP representation."""

        allowed = {
            "cpu_cores",
            "memory_mb",
            "gpu_count",
            "gpu_class",
            "wall_time_seconds",
            "numa_node",
            "cpu_affinity",
            "labels",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown resource fields: {', '.join(sorted(unknown))}")
        return cls(
            cpu_cores=_integer(value, "cpu_cores"),
            memory_mb=_integer(value, "memory_mb"),
            gpu_count=_integer(value, "gpu_count", default=0),
            gpu_class=_optional_text(value, "gpu_class"),
            wall_time_seconds=_integer(value, "wall_time_seconds", default=3600),
            numa_node=_optional_integer(value, "numa_node"),
            cpu_affinity=_integer_tuple(value, "cpu_affinity"),
            labels=_text_set(value, "labels"),
        )


@dataclass(frozen=True, slots=True)
class SchedulerGpu:
    """GPU inventory reported by one node."""

    device_id: str
    device_class: str
    numa_node: int | None


@dataclass(frozen=True, slots=True)
class SchedulerNode:
    """One complete node heartbeat."""

    node_id: str
    cpu_ids: tuple[int, ...]
    memory_mb: int
    gpus: tuple[SchedulerGpu, ...]
    numa_cpus: dict[int, tuple[int, ...]]
    labels: frozenset[str]
    heartbeat_at: int

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SchedulerNode:
        """Build and validate a node heartbeat from JSON."""

        node_id = value.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be non-empty text")
        raw_gpus = value.get("gpus", [])
        raw_numa = value.get("numa_cpus", {})
        if not isinstance(raw_gpus, list) or not isinstance(raw_numa, dict):
            raise ValueError("gpus must be a list and numa_cpus must be an object")
        gpus = tuple(_gpu_from_mapping(item) for item in raw_gpus)
        numa_cpus = {
            int(node): _integer_values(cpus, "NUMA CPU list") for node, cpus in raw_numa.items()
        }
        result = cls(
            node_id=node_id,
            cpu_ids=_integer_tuple(value, "cpu_ids", required=True),
            memory_mb=_integer(value, "memory_mb"),
            gpus=gpus,
            numa_cpus=numa_cpus,
            labels=_text_set(value, "labels"),
            heartbeat_at=_integer(value, "heartbeat_at"),
        )
        if not result.cpu_ids or result.memory_mb < 1:
            raise ValueError("node CPU and memory capacity must be positive")
        if any(cpu not in result.cpu_ids for cpus in numa_cpus.values() for cpu in cpus):
            raise ValueError("NUMA CPU lists must be subsets of cpu_ids")
        return result


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """Resource scheduling state attached to a SigRaft task."""

    task_id: str
    project: str
    resources: SchedulerResources
    priority: int
    sequence: int
    max_attempts: int
    attempts: int = 0
    state: ScheduledState = ScheduledState.QUEUED
    allocation_id: str | None = None
    node_id: str | None = None
    lease_expires_at: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the stable HTTP representation."""

        value = asdict(self)
        value["state"] = self.state.value
        value["resources"]["labels"] = sorted(self.resources.labels)
        return value


@dataclass(frozen=True, slots=True)
class SchedulerDispatch:
    """Committed placement and node-agent enforcement values."""

    allocation_id: str
    task_id: str
    node_id: str
    queue: str
    cpu_ids: tuple[int, ...]
    memory_max_bytes: int
    gpu_ids: tuple[str, ...]
    numa_node: int | None
    environment: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SigRaftScheduler:
    """Single-leader deterministic scheduler for the final service."""

    heartbeat_timeout: int = 30
    leader_id: str = "sigraft-scheduler"
    leader_term: int = 1
    _nodes: dict[str, SchedulerNode] = field(default_factory=dict)
    _jobs: dict[str, ScheduledJob] = field(default_factory=dict)
    _dispatches: dict[str, SchedulerDispatch] = field(default_factory=dict)
    _sequence: int = 0
    _revision: int = 0

    def heartbeat(self, node: SchedulerNode) -> None:
        previous = self._nodes.get(node.node_id)
        if previous is not None and node.heartbeat_at < previous.heartbeat_at:
            raise ValueError("heartbeat time must not move backwards")
        self._nodes[node.node_id] = node

    def validate_request(self, resources: SchedulerResources) -> None:
        """Reject a request that no registered node can ever satisfy."""

        if self._nodes and not any(_static_fit(resources, node) for node in self._nodes.values()):
            raise ValueError("no registered node can satisfy the resource request")

    def submit(
        self,
        task_id: str,
        *,
        project: str,
        resources: SchedulerResources,
        priority: int = 0,
        max_attempts: int = 1,
    ) -> ScheduledJob:
        if task_id in self._jobs:
            raise ValueError("task is already registered with the scheduler")
        if not project.strip() or not 0 <= priority <= 100 or max_attempts < 1:
            raise ValueError("project, priority or max_attempts is invalid")
        self.validate_request(resources)
        self._sequence += 1
        self._revision += 1
        job = ScheduledJob(
            task_id=task_id,
            project=project.strip(),
            resources=resources,
            priority=priority,
            sequence=self._sequence,
            max_attempts=max_attempts,
        )
        self._jobs[task_id] = job
        return job

    def schedule(
        self, *, caller_id: str, now: int, lease_seconds: int = 30
    ) -> tuple[SchedulerDispatch, ...]:
        if caller_id != self.leader_id:
            raise PermissionError("only the elected scheduler may place jobs")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.recover(now=now)
        result: list[SchedulerDispatch] = []
        queued = sorted(
            (job for job in self._jobs.values() if job.state is ScheduledState.QUEUED),
            key=lambda job: (-job.priority, job.sequence, job.task_id),
        )
        for job in queued:
            candidates = [
                node
                for node in self._nodes.values()
                if now - node.heartbeat_at <= self.heartbeat_timeout
                and self._available_fit(job.resources, node)
            ]
            if not candidates:
                continue
            selected = min(candidates, key=lambda node: self._score(job.resources, node))
            result.append(self._reserve(job, selected, now, lease_seconds))
        return tuple(result)

    def job(self, task_id: str) -> ScheduledJob | None:
        return self._jobs.get(task_id)

    def recover(self, *, now: int) -> None:
        for task_id, job in tuple(self._jobs.items()):
            if (
                job.state in {ScheduledState.SCHEDULED, ScheduledState.RUNNING}
                and job.lease_expires_at is not None
                and job.lease_expires_at <= now
            ):
                state = (
                    ScheduledState.QUEUED
                    if job.attempts < job.max_attempts
                    else ScheduledState.FAILED
                )
                self._jobs[task_id] = replace(
                    job,
                    state=state,
                    allocation_id=None,
                    node_id=None,
                    lease_expires_at=None,
                )
                if job.allocation_id is not None:
                    self._dispatches.pop(job.allocation_id, None)
                self._revision += 1

    def _reserve(
        self,
        job: ScheduledJob,
        node: SchedulerNode,
        now: int,
        lease_seconds: int,
    ) -> SchedulerDispatch:
        used_cpu, used_memory, used_gpu = self._used(node.node_id)
        cpus = tuple(cpu for cpu in _eligible_cpus(job.resources, node) if cpu not in used_cpu)[
            : job.resources.cpu_cores
        ]
        gpus = tuple(
            gpu.device_id
            for gpu in _eligible_gpus(job.resources, node)
            if gpu.device_id not in used_gpu
        )[: job.resources.gpu_count]
        self._revision += 1
        allocation_id = f"allocation-{self._revision}"
        dispatch = SchedulerDispatch(
            allocation_id=allocation_id,
            task_id=job.task_id,
            node_id=node.node_id,
            queue=f"sigraft.node.{node.node_id}",
            cpu_ids=cpus,
            memory_max_bytes=job.resources.memory_mb * 1024 * 1024,
            gpu_ids=gpus,
            numa_node=job.resources.numa_node,
            environment={"CUDA_VISIBLE_DEVICES": ",".join(gpus)} if gpus else {},
        )
        self._dispatches[allocation_id] = dispatch
        self._jobs[job.task_id] = replace(
            job,
            attempts=job.attempts + 1,
            state=ScheduledState.SCHEDULED,
            allocation_id=allocation_id,
            node_id=node.node_id,
            lease_expires_at=now + lease_seconds,
        )
        return dispatch

    def _available_fit(self, resources: SchedulerResources, node: SchedulerNode) -> bool:
        if not _static_fit(resources, node):
            return False
        used_cpu, used_memory, used_gpu = self._used(node.node_id)
        return (
            sum(cpu not in used_cpu for cpu in _eligible_cpus(resources, node))
            >= resources.cpu_cores
            and node.memory_mb - used_memory >= resources.memory_mb
            and sum(gpu.device_id not in used_gpu for gpu in _eligible_gpus(resources, node))
            >= resources.gpu_count
        )

    def _score(self, resources: SchedulerResources, node: SchedulerNode) -> tuple[int, int, str]:
        used_cpu, used_memory, _ = self._used(node.node_id)
        return (
            len(node.cpu_ids) - len(used_cpu) - resources.cpu_cores,
            node.memory_mb - used_memory - resources.memory_mb,
            node.node_id,
        )

    def _used(self, node_id: str) -> tuple[set[int], int, set[str]]:
        dispatches = [
            dispatch for dispatch in self._dispatches.values() if dispatch.node_id == node_id
        ]
        jobs = {job.task_id: job for job in self._jobs.values()}
        return (
            {cpu for dispatch in dispatches for cpu in dispatch.cpu_ids},
            sum(jobs[dispatch.task_id].resources.memory_mb for dispatch in dispatches),
            {gpu for dispatch in dispatches for gpu in dispatch.gpu_ids},
        )


def _eligible_cpus(resources: SchedulerResources, node: SchedulerNode) -> tuple[int, ...]:
    cpus = resources.cpu_affinity or node.cpu_ids
    if resources.numa_node is not None:
        allowed = set(node.numa_cpus.get(resources.numa_node, ()))
        cpus = tuple(cpu for cpu in cpus if cpu in allowed)
    return tuple(sorted(cpus))


def _eligible_gpus(resources: SchedulerResources, node: SchedulerNode) -> tuple[SchedulerGpu, ...]:
    return tuple(
        gpu
        for gpu in node.gpus
        if resources.gpu_class is None or gpu.device_class == resources.gpu_class
        if resources.numa_node is None or gpu.numa_node == resources.numa_node
    )


def _static_fit(resources: SchedulerResources, node: SchedulerNode) -> bool:
    return (
        resources.labels <= node.labels
        and resources.memory_mb <= node.memory_mb
        and len(_eligible_cpus(resources, node)) >= resources.cpu_cores
        and len(_eligible_gpus(resources, node)) >= resources.gpu_count
    )


def _integer(value: dict[str, Any], key: str, *, default: int | None = None) -> int:
    item = value.get(key, default)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{key} must be an integer")
    return item


def _optional_integer(value: dict[str, Any], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{key} must be an integer")
    return item


def _optional_text(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be non-empty text")
    return item


def _integer_values(value: object, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise ValueError(f"{label} must contain integers")
    return tuple(value)


def _integer_tuple(value: dict[str, Any], key: str, *, required: bool = False) -> tuple[int, ...]:
    item = value.get(key)
    if item is None and not required:
        return ()
    return _integer_values(item, key)


def _text_set(value: dict[str, Any], key: str) -> frozenset[str]:
    item = value.get(key, [])
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise ValueError(f"{key} must contain text values")
    return frozenset(item)


def _gpu_from_mapping(value: object) -> SchedulerGpu:
    if not isinstance(value, dict):
        raise ValueError("each GPU must be an object")
    device_id = value.get("device_id")
    device_class = value.get("device_class")
    numa_node = value.get("numa_node")
    if not isinstance(device_id, str) or not isinstance(device_class, str):
        raise ValueError("GPU id and class must be text")
    if numa_node is not None and (isinstance(numa_node, bool) or not isinstance(numa_node, int)):
        raise ValueError("GPU numa_node must be an integer")
    return SchedulerGpu(device_id, device_class, numa_node)
