"""Tests for the lab_19_raft_election package."""

from __future__ import annotations

from lab_19_raft_election import (
    DeterministicRaftCluster,
    LogPosition,
    NodeRole,
    RequestVote,
    __version__,
)


def test_timeout_input_elects_a_single_leader() -> None:
    cluster = DeterministicRaftCluster(("n1", "n2", "n3"))

    result = cluster.trigger_timeout("n1")
    heartbeats = cluster.send_heartbeat("n1")

    assert result.became_leader
    assert result.term == 1
    assert result.granted_by == ("n1", "n2", "n3")
    assert cluster.node("n1").role is NodeRole.LEADER
    assert {reply.follower_id for reply in heartbeats} == {"n2", "n3"}
    assert all(reply.accepted for reply in heartbeats)
    assert cluster.node("n2").leader_id == "n1"
    assert cluster.leader_for_term(1) == "n1"


def test_vote_is_persisted_across_a_restart() -> None:
    cluster = DeterministicRaftCluster(("n1", "n2", "n3"))
    voter = cluster.node("n2")

    granted = voter.receive_vote_request(
        RequestVote(term=1, candidate_id="n1", last_log_position=LogPosition())
    )
    cluster.restart_node("n2")
    denied = cluster.node("n2").receive_vote_request(
        RequestVote(term=1, candidate_id="n3", last_log_position=LogPosition())
    )

    assert granted.granted
    assert not denied.granted
    assert cluster.node("n2").voted_for == "n1"


def test_split_vote_recovers_when_a_new_timeout_fires() -> None:
    cluster = DeterministicRaftCluster(("n1", "n2", "n3", "n4"))
    cluster.set_partition((("n1", "n2"), ("n3", "n4")))

    first_half = cluster.trigger_timeout("n1")
    second_half = cluster.trigger_timeout("n3")
    cluster.heal()
    retry = cluster.trigger_timeout("n3")

    assert not first_half.became_leader
    assert not second_half.became_leader
    assert first_half.granted_by == ("n1", "n2")
    assert second_half.granted_by == ("n3", "n4")
    assert retry.became_leader
    assert retry.term == 2
    assert cluster.leader_for_term(2) == "n3"


def test_higher_term_candidate_forces_old_leader_to_step_down() -> None:
    cluster = DeterministicRaftCluster(("n1", "n2", "n3"))
    cluster.trigger_timeout("n1")

    replacement = cluster.trigger_timeout("n2")

    assert replacement.became_leader
    assert replacement.term == 2
    assert cluster.node("n1").role is NodeRole.FOLLOWER
    assert cluster.node("n1").current_term == 2
    assert cluster.node("n2").role is NodeRole.LEADER


def test_minority_partition_cannot_elect_but_majority_can() -> None:
    cluster = DeterministicRaftCluster(("n1", "n2", "n3", "n4", "n5"))
    cluster.set_partition((("n1", "n2"), ("n3", "n4", "n5")))

    minority = cluster.trigger_timeout("n1")
    majority = cluster.trigger_timeout("n3")

    assert not minority.became_leader
    assert minority.granted_by == ("n1", "n2")
    assert majority.became_leader
    assert majority.granted_by == ("n3", "n4", "n5")


def test_version_is_exposed() -> None:
    assert __version__
