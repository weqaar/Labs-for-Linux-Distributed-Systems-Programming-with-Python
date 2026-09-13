"""Tests for the lab_25_replicated_log package."""

from __future__ import annotations

import pytest

from lab_25_replicated_log import (
    CompleteTask,
    EnqueueTask,
    NodeRole,
    RelayTaskStatus,
    ReplicatedRelayCluster,
    SimulationClock,
    StartTask,
    __version__,
)


def make_cluster() -> ReplicatedRelayCluster:
    cluster = ReplicatedRelayCluster(("n1", "n2", "n3"))
    role = cluster.trigger_timeout("n1")
    assert role is NodeRole.LEADER
    return cluster


def test_majority_commit_applies_commands_in_order() -> None:
    cluster = make_cluster()
    clock = SimulationClock()
    enqueue = EnqueueTask(task_id="task-1", queue="default", created_tick=clock.advance())
    start = StartTask(task_id="task-1", worker_id="worker-a", attempt=1)
    complete = CompleteTask(
        task_id="task-1",
        worker_id="worker-a",
        completed_tick=clock.advance(),
    )

    enqueue_result = cluster.submit_command("n1", enqueue)
    start_result = cluster.submit_command("n1", start)
    complete_result = cluster.submit_command("n1", complete)
    leader = cluster.node("n1")

    assert enqueue_result.committed
    assert start_result.committed
    assert complete_result.committed
    assert leader.commit_index == 3
    assert leader.state_machine.history() == (enqueue, start, complete)
    leader_task = leader.state_machine.task("task-1")
    follower_task = cluster.node("n2").state_machine.task("task-1")
    assert leader_task is not None
    assert follower_task is not None
    assert leader_task.status is RelayTaskStatus.COMPLETED
    assert follower_task.status is RelayTaskStatus.COMPLETED


def test_follower_catches_up_after_being_down_for_the_whole_write_sequence() -> None:
    cluster = make_cluster()
    clock = SimulationClock()
    cluster.crash_node("n3")

    cluster.submit_command(
        "n1",
        EnqueueTask(task_id="task-2", queue="payments", created_tick=clock.advance()),
    )
    cluster.submit_command("n1", StartTask(task_id="task-2", worker_id="worker-b", attempt=1))
    cluster.restart_node("n3")
    cluster.replicate_from_leader("n1")

    assert cluster.node("n3").log == cluster.node("n1").log
    assert (
        cluster.node("n3").state_machine.snapshot() == cluster.node("n1").state_machine.snapshot()
    )


def test_committed_state_survives_restart_from_persisted_log() -> None:
    cluster = make_cluster()
    clock = SimulationClock()
    cluster.submit_command(
        "n1",
        EnqueueTask(task_id="task-3", queue="audit", created_tick=clock.advance()),
    )
    cluster.crash_node("n2")
    restarted = cluster.restart_node("n2")

    task = restarted.state_machine.task("task-3")

    assert task is not None
    assert task.status is RelayTaskStatus.QUEUED


def test_uncommitted_leader_entry_is_never_applied_and_is_overwritten() -> None:
    cluster = make_cluster()
    clock = SimulationClock()
    cluster.set_partition((("n1",), ("n2", "n3")))

    isolated_result = cluster.submit_command(
        "n1",
        EnqueueTask(task_id="task-isolated", queue="edge", created_tick=clock.advance()),
    )

    assert not isolated_result.committed
    assert cluster.node("n1").state_machine.task("task-isolated") is None

    replacement_role = cluster.trigger_timeout("n2")
    cluster.submit_command(
        "n2",
        EnqueueTask(task_id="task-majority", queue="edge", created_tick=clock.advance()),
    )
    cluster.heal()
    cluster.replicate_from_leader("n2")

    assert replacement_role is NodeRole.LEADER
    assert cluster.node("n1").state_machine.task("task-isolated") is None
    assert cluster.node("n1").state_machine.task("task-majority") is not None
    assert [entry.command.task_id for entry in cluster.node("n1").log] == ["task-majority"]


def test_only_a_leader_can_accept_client_commands() -> None:
    cluster = ReplicatedRelayCluster(("n1", "n2", "n3"))

    with pytest.raises(ValueError, match="not the current leader"):
        cluster.submit_command(
            "n1",
            EnqueueTask(task_id="task-4", queue="default", created_tick=1),
        )


def test_version_is_exposed() -> None:
    assert __version__
