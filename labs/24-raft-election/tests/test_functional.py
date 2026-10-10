"""Functional tests for the Raft election simulation.

These tests drive a whole simulated cluster through the public
``DeterministicRaftCluster`` interface: fire chosen timeouts, send heartbeats,
partition and heal the scripted network, and read each node's role and term.
The test decides which timeout fires next, so no wall clock or real network is
used.
"""

from __future__ import annotations

import pytest

from lab_24_raft_election import DeterministicRaftCluster, NodeRole

NODES = ("n1", "n2", "n3", "n4", "n5")


def roles(cluster: DeterministicRaftCluster) -> dict[str, NodeRole]:
    return {node_id: cluster.node(node_id).role for node_id in cluster.node_ids}


def test_cluster_elects_a_leader_loses_it_and_elects_a_replacement() -> None:
    cluster = DeterministicRaftCluster(NODES)

    first = cluster.trigger_timeout("n1")
    accepted = cluster.send_heartbeat("n1")
    assert first.became_leader
    assert all(reply.accepted for reply in accepted)
    assert {cluster.node(node).leader_id for node in NODES} == {"n1"}

    cluster.set_partition((("n1",), ("n2", "n3", "n4", "n5")))
    assert cluster.send_heartbeat("n1") == ()
    replacement = cluster.trigger_timeout("n2")
    assert replacement.became_leader
    assert replacement.term == 2
    assert replacement.granted_by == ("n2", "n3", "n4", "n5")

    cluster.heal()
    rejected = cluster.send_heartbeat("n1")
    assert rejected
    assert not any(reply.accepted for reply in rejected)
    assert cluster.node("n1").role is NodeRole.FOLLOWER
    assert cluster.node("n1").current_term == 2

    cluster.send_heartbeat("n2")
    assert roles(cluster) == {
        "n1": NodeRole.FOLLOWER,
        "n2": NodeRole.LEADER,
        "n3": NodeRole.FOLLOWER,
        "n4": NodeRole.FOLLOWER,
        "n5": NodeRole.FOLLOWER,
    }
    assert {cluster.node(node).leader_id for node in NODES} == {"n2"}
    assert (cluster.leader_for_term(1), cluster.leader_for_term(2)) == ("n1", "n2")


def test_cluster_split_three_ways_elects_nobody_until_the_partition_heals() -> None:
    cluster = DeterministicRaftCluster(NODES)
    cluster.set_partition((("n1", "n2"), ("n3", "n4"), ("n5",)))

    attempts = [cluster.trigger_timeout(node) for node in ("n1", "n3", "n5")]

    assert not any(attempt.became_leader for attempt in attempts)
    assert NodeRole.LEADER not in roles(cluster).values()
    assert cluster.leader_for_term(1) is None

    cluster.heal()
    recovery = cluster.trigger_timeout("n5")

    assert recovery.became_leader
    assert recovery.term == 2
    assert list(roles(cluster).values()).count(NodeRole.LEADER) == 1
    assert cluster.leader_for_term(2) == "n5"


def test_heartbeat_from_a_node_that_is_not_leader_is_refused() -> None:
    cluster = DeterministicRaftCluster(("n1", "n2", "n3"))
    cluster.trigger_timeout("n1")

    with pytest.raises(ValueError, match="n2 is not the current leader"):
        cluster.send_heartbeat("n2")
    with pytest.raises(ValueError, match="unknown node n9"):
        cluster.trigger_timeout("n9")
    with pytest.raises(ValueError, match="at least three nodes"):
        DeterministicRaftCluster(("n1", "n2"))

    assert cluster.node("n1").role is NodeRole.LEADER
