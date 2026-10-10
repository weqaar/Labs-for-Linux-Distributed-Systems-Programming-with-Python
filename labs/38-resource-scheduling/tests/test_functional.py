"""Functional tests for the SigRaft resource scheduler.

These tests drive a composed `ResourceScheduler` through its public methods
across whole job workflows: a node reports inventory, a leader is assigned,
a job such as `task-17` is submitted, scheduled, started and completed. Time
is a supplied integer and node inventories are supplied values, so the tests
never read or change the host's cgroups, CPUs or GPUs.
"""

from __future__ import annotations

import pytest

from lab_38_resource_scheduling import (
    InvalidTransitionError,
    JobState,
    LeadershipError,
    NodeInventory,
    ResourceRequest,
    ResourceScheduler,
)

LEADER = "scheduler-a"


@pytest.fixture
def scheduler() -> ResourceScheduler:
    active = ResourceScheduler(heartbeat_timeout=30)
    active.heartbeat(NodeInventory("node-a", (0, 1, 2, 3), 4096, heartbeat_at=100))
    active.become_leader(LEADER, 1)
    return active


def test_submitted_job_is_reserved_dispatched_and_runs_to_succeeded(
    scheduler: ResourceScheduler,
) -> None:
    job = scheduler.submit(
        "task-17",
        project="research",
        action="simulate",
        request=ResourceRequest(cpu_cores=2, memory_mb=1024),
    )
    assert job.state is JobState.QUEUED

    (plan,) = scheduler.schedule(caller_id=LEADER, now=100)

    assert scheduler.job("task-17").state is JobState.SCHEDULED
    assert (plan.job_id, plan.queue, plan.cgroup) == (
        "task-17",
        "sigraft.node.node-a",
        "sigraft/task-17",
    )
    assert (plan.cpu_set, plan.memory_max_bytes) == ("0,1", 1024 * 1024 * 1024)
    assert scheduler.log.entries[-1].event == "reserved"
    assert scheduler.mark_running(plan.allocation_id, caller_id=LEADER).state is JobState.RUNNING
    done = scheduler.complete(plan.allocation_id, caller_id=LEADER, succeeded=True)
    assert done.state is JobState.SUCCEEDED
    assert scheduler.allocations == ()
    assert [entry.event for entry in scheduler.log.entries] == [
        "submitted",
        "reserved",
        "running",
        "succeeded",
    ]


def test_queued_job_waits_for_capacity_and_starts_after_the_first_job_fails(
    scheduler: ResourceScheduler,
) -> None:
    whole_node = ResourceRequest(cpu_cores=4, memory_mb=4096)
    scheduler.submit("task-17", project="research", action="first", request=whole_node)
    scheduler.submit("task-18", project="research", action="second", request=whole_node)

    (first,) = scheduler.schedule(caller_id=LEADER, now=100)
    assert first.job_id == "task-17"
    assert scheduler.job("task-18").state is JobState.QUEUED
    assert scheduler.schedule(caller_id=LEADER, now=101) == ()

    scheduler.mark_running(first.allocation_id, caller_id=LEADER)
    failed = scheduler.complete(
        first.allocation_id, caller_id=LEADER, succeeded=False, error="exit status 1"
    )
    (second,) = scheduler.schedule(caller_id=LEADER, now=102)

    assert (failed.state, failed.last_error) == (JobState.FAILED, "exit status 1")
    assert second.job_id == "task-18"
    assert second.cpu_set == "0,1,2,3"
    assert scheduler.job("task-18").state is JobState.SCHEDULED


def test_duplicate_task_id_is_rejected_and_only_the_first_job_is_scheduled(
    scheduler: ResourceScheduler,
) -> None:
    request = ResourceRequest(cpu_cores=1, memory_mb=256)
    scheduler.submit("task-17", project="research", action="simulate", request=request)

    with pytest.raises(ValueError, match="already exists"):
        scheduler.submit("task-17", project="research", action="again", request=request)

    plans = scheduler.schedule(caller_id=LEADER, now=100)
    assert [plan.job_id for plan in plans] == ["task-17"]
    assert scheduler.job("task-17").action == "simulate"
    assert [entry.event for entry in scheduler.log.entries] == ["submitted", "reserved"]


def test_former_leader_is_rejected_after_failover_and_new_leader_finishes_the_job(
    scheduler: ResourceScheduler,
) -> None:
    scheduler.submit(
        "task-17",
        project="research",
        action="simulate",
        request=ResourceRequest(cpu_cores=1, memory_mb=512),
    )
    (plan,) = scheduler.schedule(caller_id=LEADER, now=100)

    scheduler.become_leader("scheduler-b", 2)

    with pytest.raises(LeadershipError, match="elected scheduler"):
        scheduler.mark_running(plan.allocation_id, caller_id=LEADER)
    assert scheduler.job("task-17").state is JobState.SCHEDULED
    scheduler.mark_running(plan.allocation_id, caller_id="scheduler-b")
    done = scheduler.complete(plan.allocation_id, caller_id="scheduler-b", succeeded=True)
    assert done.state is JobState.SUCCEEDED
    assert scheduler.log.entries[-1].leader_term == 2
    with pytest.raises(InvalidTransitionError, match="terminal"):
        scheduler.cancel("task-17", caller_id="scheduler-b")
