"""Functional tests for the relay connection diagnosis workflow.

These are functional tests. They drive the public package interface of
``lab_13_packet_tools`` the way an operator diagnosing a failed connection to
``relay`` would: build the probe plan for a job, collect one reply (or a
timeout) per probe step, parse each captured packet and classify it. The
replies come from a deterministic fake network that constructs packets
offline, so no raw socket, root permission or real network is needed.
"""

# pyright: strict

from __future__ import annotations

from collections.abc import Callable

import pytest

import lab_13_packet_tools as lab

Reply = bytes | lab.TimeoutEvidence
Responder = Callable[[lab.ProbePlan], Reply]


def healthy_relay_host(step: lab.ProbePlan) -> Reply:
    """Answer like a reachable host where relay listens only on its service port."""
    if step.protocol is lab.ProbeProtocol.UDP:
        return lab.TimeoutEvidence(seconds=step.timeout_seconds)
    if step.port == 7000:
        return lab.build_ipv4_tcp_packet(
            source_port=step.port,
            destination_port=41000,
            flags=lab.TcpFlag.SYN | lab.TcpFlag.ACK,
        )
    return lab.build_ipv4_tcp_packet(
        source_port=step.port,
        destination_port=41001,
        flags=lab.TcpFlag.RST | lab.TcpFlag.ACK,
    )


def firewalled_relay_host(step: lab.ProbePlan) -> Reply:
    """Answer like a host behind a router that rejects every probe administratively."""
    return lab.build_ipv4_icmp_packet(icmp_type=3, code=13, payload=b"relay")


def diagnose(task: lab.RelayTask, responder: Responder) -> dict[str, lab.ProbeOutcome]:
    """Run the full probe plan against a fake host and classify every reply."""
    outcomes: dict[str, lab.ProbeOutcome] = {}
    for step in lab.build_relay_probe_plan(task, host="relay.internal", service_port=7000):
        reply = responder(step)
        evidence = (
            reply if isinstance(reply, lab.TimeoutEvidence) else lab.parse_probe_packet(reply)
        )
        outcomes[step.name] = lab.classify_probe_evidence(evidence)
    return outcomes


def test_healthy_relay_host_matches_every_expected_probe_outcome() -> None:
    task = lab.RelayTask("task-17", "diagnose-relay", lab.TaskState.RUNNING)
    plan = lab.build_relay_probe_plan(task, host="relay.internal", service_port=7000)

    outcomes = diagnose(task, healthy_relay_host)

    assert outcomes == {step.name: step.expected_outcome for step in plan}
    assert outcomes["open-service-port"] is lab.ProbeOutcome.OPEN
    assert outcomes["closed-reference-port"] is lab.ProbeOutcome.REFUSED
    assert outcomes["filtered-or-silent-drop"] is lab.ProbeOutcome.TIMEOUT


def test_firewalled_host_is_reported_as_filtered_rather_than_refused_or_silent() -> None:
    task = lab.RelayTask("task-17", "diagnose-relay", lab.TaskState.FAILED)

    outcomes = diagnose(task, firewalled_relay_host)

    assert set(outcomes.values()) == {lab.ProbeOutcome.FILTERED_OR_UNREACHABLE}


def test_truncated_capture_is_rejected_instead_of_being_classified() -> None:
    task = lab.RelayTask("task-17", "diagnose-relay")

    def truncating_host(step: lab.ProbePlan) -> Reply:
        packet = healthy_relay_host(step)
        if isinstance(packet, lab.TimeoutEvidence):
            return packet
        return packet[:30]

    with pytest.raises(lab.PacketFormatError, match="truncated|invalid|requires"):
        diagnose(task, truncating_host)


def test_probe_plan_rejects_a_non_positive_timeout() -> None:
    task = lab.RelayTask("task-17", "diagnose-relay")

    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        lab.build_relay_probe_plan(
            task, host="relay.internal", service_port=7000, timeout_seconds=0
        )
