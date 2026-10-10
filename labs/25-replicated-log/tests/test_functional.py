"""Functional tests for the replicated relay log.

These tests drive a whole simulated cluster through the public
``ReplicatedRelayCluster`` interface: elect a leader, submit job commands,
crash and restart nodes, and read each replica's applied state. Timeouts and
crashes are chosen by the test, so no wall clock or real network is used.
"""

from __future__ import annotations

from lab_25_replicated_log import (
    CompleteTask,
    EnqueueTask,
    NodeRole,
    RelayTaskStatus,
    ReplicatedRelayCluster,
    SimulationClock,
    StartTask,
)

NODES = ("n1", "n2", "n3", "n4", "n5")


def status_on(cluster: ReplicatedRelayCluster, node_id: str, task_id: str) -> RelayTaskStatus:
    task = cluster.node(node_id).state_machine.task(task_id)
    assert task is not None, f"{task_id} is not applied on {node_id}"
    return task.status


def test_submitted_job_replicates_to_a_majority_and_runs_to_completion() -> None:
    cluster = ReplicatedRelayCluster(NODES)
    clock = SimulationClock()
    assert cluster.trigger_timeout("n1") is NodeRole.LEADER
    cluster.crash_node("n5")

    enqueued = cluster.submit_command(
        "n1", EnqueueTask(task_id="task-17", queue="default", created_tick=clock.advance())
    )
    assert enqueued.committed
    assert enqueued.rejection is None
    assert enqueued.replicated_to == ("n1", "n2", "n3", "n4")
    assert {status_on(cluster, node, "task-17") for node in NODES[:4]} == {RelayTaskStatus.QUEUED}

    started = cluster.submit_command(
        "n1", StartTask(task_id="task-17", worker_id="worker-a", attempt=1)
    )
    assert started.committed
    assert status_on(cluster, "n3", "task-17") is RelayTaskStatus.RUNNING

    completed = cluster.submit_command(
        "n1", CompleteTask(task_id="task-17", worker_id="worker-a", completed_tick=clock.advance())
    )
    assert completed.committed
    assert (enqueued.index, started.index, completed.index) == (1, 2, 3)
    assert {status_on(cluster, node, "task-17") for node in NODES[:4]} == {
        RelayTaskStatus.COMPLETED
    }
    assert cluster.node("n5").state_machine.task("task-17") is None

    cluster.restart_node("n5")
    cluster.replicate_from_leader("n1")

    assert cluster.node("n5").log == cluster.node("n1").log
    assert status_on(cluster, "n5", "task-17") is RelayTaskStatus.COMPLETED


def test_replacement_leader_keeps_a_committed_job_after_the_leader_crashes() -> None:
    cluster = ReplicatedRelayCluster(NODES)
    clock = SimulationClock()
    assert cluster.trigger_timeout("n1") is NodeRole.LEADER
    cluster.submit_command(
        "n1", EnqueueTask(task_id="task-17", queue="default", created_tick=clock.advance())
    )

    cluster.crash_node("n1")
    assert cluster.trigger_timeout("n2") is NodeRole.LEADER
    cluster.submit_command("n2", StartTask(task_id="task-17", worker_id="worker-b", attempt=1))
    finished = cluster.submit_command(
        "n2", CompleteTask(task_id="task-17", worker_id="worker-b", completed_tick=clock.advance())
    )
    cluster.restart_node("n1")
    cluster.replicate_from_leader("n2")

    assert finished.committed
    assert finished.term == 2
    assert cluster.node("n1").role is NodeRole.FOLLOWER
    assert cluster.node("n1").leader_id == "n2"
    assert status_on(cluster, "n1", "task-17") is RelayTaskStatus.COMPLETED
    assert cluster.node("n1").state_machine.history() == cluster.node("n2").state_machine.history()


def test_duplicate_job_id_is_rejected_on_every_replica_without_stalling_the_log() -> None:
    cluster = ReplicatedRelayCluster(("n1", "n2", "n3"))
    assert cluster.trigger_timeout("n1") is NodeRole.LEADER
    cluster.submit_command("n1", EnqueueTask(task_id="task-17", queue="default", created_tick=1))

    duplicate = cluster.submit_command(
        "n1", EnqueueTask(task_id="task-17", queue="other", created_tick=2)
    )
    following = cluster.submit_command(
        "n1", EnqueueTask(task_id="task-18", queue="default", created_tick=3)
    )

    assert duplicate.committed
    assert duplicate.rejection == "task-17 already exists"
    assert following.committed
    assert following.rejection is None
    for node_id in ("n1", "n2", "n3"):
        node = cluster.node(node_id)
        assert node.rejection_for(duplicate.index) == "task-17 already exists"
        task = node.state_machine.task("task-17")
        assert task is not None
        assert task.queue == "default"
        assert status_on(cluster, node_id, "task-18") is RelayTaskStatus.QUEUED


def test_job_submitted_without_a_reachable_majority_never_becomes_visible() -> None:
    cluster = ReplicatedRelayCluster(NODES)
    assert cluster.trigger_timeout("n1") is NodeRole.LEADER
    cluster.set_partition((("n1", "n2"), ("n3", "n4", "n5")))

    stranded = cluster.submit_command(
        "n1", EnqueueTask(task_id="task-17", queue="default", created_tick=1)
    )

    assert not stranded.committed
    assert stranded.replicated_to == ("n1", "n2")
    assert all(cluster.node(node).state_machine.task("task-17") is None for node in NODES)

    assert cluster.trigger_timeout("n3") is NodeRole.LEADER
    cluster.heal()
    cluster.replicate_from_leader("n3")

    assert cluster.node("n1").role is NodeRole.FOLLOWER
    assert cluster.node("n1").log == ()
    assert all(cluster.node(node).state_machine.task("task-17") is None for node in NODES)
