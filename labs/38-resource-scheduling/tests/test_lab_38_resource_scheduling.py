"""Tests for deterministic SigRaft resource scheduling."""

from __future__ import annotations

import pytest

from lab_38_resource_scheduling import (
    GpuDevice,
    InvalidTransitionError,
    JobState,
    LeadershipError,
    NodeInventory,
    ProjectQuota,
    ResourceRequest,
    ResourceScheduler,
    UnschedulableRequestError,
    __version__,
)


def node(
    node_id: str,
    *,
    heartbeat_at: int = 100,
    memory_mb: int = 16_384,
    labels: frozenset[str] = frozenset({"linux", "compute"}),
) -> NodeInventory:
    return NodeInventory(
        node_id=node_id,
        cpu_ids=tuple(range(8)),
        memory_mb=memory_mb,
        gpus=(
            GpuDevice("gpu-0", "a100", 0),
            GpuDevice("gpu-1", "a100", 1),
        ),
        numa_cpus={0: (0, 1, 2, 3), 1: (4, 5, 6, 7)},
        labels=labels,
        heartbeat_at=heartbeat_at,
    )


def scheduler() -> ResourceScheduler:
    result = ResourceScheduler(heartbeat_timeout=20)
    result.heartbeat(node("node-b"))
    result.heartbeat(node("node-a"))
    result.become_leader("scheduler-1", 7)
    return result


def request(**changes: object) -> ResourceRequest:
    values: dict[str, object] = {"cpu_cores": 2, "memory_mb": 1024}
    values.update(changes)
    return ResourceRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"cpu_cores": 0}, "cpu_cores"),
        ({"memory_mb": 0}, "memory_mb"),
        ({"gpu_count": -1}, "gpu_count"),
        ({"gpu_class": "a100"}, "gpu_class"),
        ({"wall_time_seconds": 0}, "wall_time_seconds"),
        ({"cpu_affinity": (0,)}, "cpu_affinity"),
    ],
)
def test_resource_request_rejects_invalid_values(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        request(**changes)


def test_admission_rejects_impossible_but_queues_temporarily_unavailable_work() -> None:
    empty = ResourceScheduler()
    queued = empty.submit(
        "task-17",
        project="science",
        action="simulate",
        request=request(cpu_cores=64),
    )
    assert queued.state is JobState.QUEUED

    active = scheduler()
    with pytest.raises(UnschedulableRequestError, match="no registered node"):
        active.submit(
            "task-18",
            project="science",
            action="simulate",
            request=request(cpu_cores=64),
        )


def test_only_elected_leader_can_schedule() -> None:
    active = scheduler()
    active.submit("task-17", project="science", action="simulate", request=request())

    with pytest.raises(LeadershipError, match="elected scheduler"):
        active.schedule(caller_id="scheduler-2", now=100)


def test_priority_fifo_and_node_tie_breaking_are_deterministic() -> None:
    active = scheduler()
    active.submit(
        "task-17",
        project="science",
        action="low",
        request=request(),
        priority=10,
    )
    active.submit(
        "task-18",
        project="science",
        action="first high",
        request=request(),
        priority=90,
    )
    active.submit(
        "task-19",
        project="science",
        action="second high",
        request=request(),
        priority=90,
    )

    plans = active.schedule(caller_id="scheduler-1", now=100)

    assert [plan.job_id for plan in plans] == ["task-18", "task-19", "task-17"]
    assert plans[0].queue == "sigraft.node.node-a"
    assert plans[0].cpu_set == "0,1"


def test_numa_gpu_request_produces_enforceable_dispatch_plan() -> None:
    active = scheduler()
    active.submit(
        "task-17",
        project="science",
        action="train",
        request=request(
            gpu_count=1,
            gpu_class="a100",
            numa_node=1,
            cpu_affinity=(4, 5, 6, 7),
        ),
    )

    plan = active.schedule(caller_id="scheduler-1", now=100)[0]
    allocation = active.allocations[0]

    assert allocation.cpu_ids == (4, 5)
    assert allocation.gpu_ids == ("gpu-1",)
    assert plan.numa_node == 1
    assert plan.memory_max_bytes == 1024 * 1024 * 1024
    assert plan.environment == {"CUDA_VISIBLE_DEVICES": "gpu-1"}


def test_stale_nodes_are_not_eligible_until_their_heartbeat_is_refreshed() -> None:
    active = ResourceScheduler(heartbeat_timeout=10)
    active.heartbeat(node("node-a", heartbeat_at=50))
    active.become_leader("scheduler-1", 1)
    active.submit("task-17", project="science", action="simulate", request=request())

    assert active.schedule(caller_id="scheduler-1", now=100) == ()
    active.heartbeat(node("node-a", heartbeat_at=100))
    assert len(active.schedule(caller_id="scheduler-1", now=100)) == 1


def test_project_quota_defers_work_without_rejecting_it() -> None:
    active = ResourceScheduler(
        quotas={"science": ProjectQuota(jobs=1, cpu_cores=2, memory_mb=2048, gpu_count=0)}
    )
    active.heartbeat(node("node-a"))
    active.become_leader("scheduler-1", 1)
    active.submit("task-17", project="science", action="first", request=request())
    active.submit("task-18", project="science", action="second", request=request())

    plans = active.schedule(caller_id="scheduler-1", now=100)

    assert [plan.job_id for plan in plans] == ["task-17"]
    assert active.job("task-18").state is JobState.QUEUED


def test_atomic_reservation_precedes_dispatch_and_tracks_leader_term() -> None:
    active = scheduler()
    active.submit("task-17", project="science", action="simulate", request=request())

    plan = active.schedule(caller_id="scheduler-1", now=100, lease_seconds=15)[0]
    allocation = active.allocations[0]

    assert active.log.entries[-1].event == "reserved"
    assert allocation.leader_term == 7
    assert allocation.lease_expires_at == 115
    assert plan.allocation_id == allocation.allocation_id


def test_lifecycle_releases_resources_after_success() -> None:
    active = scheduler()
    active.submit("task-17", project="science", action="simulate", request=request())
    allocation_id = active.schedule(caller_id="scheduler-1", now=100)[0].allocation_id

    running = active.mark_running(allocation_id, caller_id="scheduler-1")
    completed = active.complete(
        allocation_id,
        caller_id="scheduler-1",
        succeeded=True,
    )

    assert running.state is JobState.RUNNING
    assert completed.state is JobState.SUCCEEDED
    assert active.allocations == ()
    with pytest.raises(InvalidTransitionError, match="terminal"):
        active.cancel("task-17", caller_id="scheduler-1")


def test_expired_lease_requeues_once_then_fails_at_attempt_limit() -> None:
    active = scheduler()
    active.submit(
        "task-17",
        project="science",
        action="simulate",
        request=request(),
        max_attempts=2,
    )
    active.schedule(caller_id="scheduler-1", now=100, lease_seconds=5)
    recovered = active.recover_expired(caller_id="scheduler-1", now=105)
    assert recovered[0].state is JobState.QUEUED

    active.schedule(caller_id="scheduler-1", now=106, lease_seconds=5)
    recovered = active.recover_expired(caller_id="scheduler-1", now=111)
    assert recovered[0].state is JobState.FAILED
    assert recovered[0].last_error == "allocation lease expired"


def test_version_is_exposed() -> None:
    assert __version__
