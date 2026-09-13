"""Tests for the lab_13_packet_tools package."""

from __future__ import annotations

import pytest

from lab_13_packet_tools import (
    ProbeOutcome,
    ProbeProtocol,
    RelayTask,
    TaskState,
    TcpFlag,
    TimeoutEvidence,
    __version__,
    build_ipv4_icmp_packet,
    build_ipv4_tcp_packet,
    build_ipv4_udp_packet,
    build_relay_probe_plan,
    classify_probe_evidence,
    parse_network_packet,
    parse_probe_packet,
)


class PacketLike:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __bytes__(self) -> bytes:
        return self._payload


def test_relay_probe_plan_has_three_distinct_steps() -> None:
    task = RelayTask("task-09", "diagnose-relay", TaskState.RUNNING)

    plan = build_relay_probe_plan(task, host="relay.internal", service_port=7000)

    assert [step.protocol for step in plan] == [
        ProbeProtocol.TCP,
        ProbeProtocol.TCP,
        ProbeProtocol.UDP,
    ]
    assert [step.expected_outcome for step in plan] == [
        ProbeOutcome.OPEN,
        ProbeOutcome.REFUSED,
        ProbeOutcome.TIMEOUT,
    ]
    assert all(step.task == task for step in plan)


def test_syn_ack_classifies_as_open() -> None:
    packet = build_ipv4_tcp_packet(
        source_port=7000,
        destination_port=41000,
        flags=TcpFlag.SYN | TcpFlag.ACK,
        payload=b"relay",
    )

    parsed = parse_network_packet(packet)

    assert parsed.tcp is not None
    assert parsed.tcp.payload == b"relay"
    assert classify_probe_evidence(parsed) is ProbeOutcome.OPEN


def test_rst_classifies_as_refused() -> None:
    packet = build_ipv4_tcp_packet(
        source_port=7001,
        destination_port=41001,
        flags=TcpFlag.RST | TcpFlag.ACK,
    )

    assert classify_probe_evidence(parse_network_packet(packet)) is ProbeOutcome.REFUSED


def test_udp_payload_and_byte_order_are_preserved() -> None:
    packet = build_ipv4_udp_packet(
        source_port=53000,
        destination_port=7000,
        payload=b"relay-ping",
    )

    parsed = parse_network_packet(packet)

    assert parsed.udp is not None
    assert parsed.udp.source_port == 53000
    assert parsed.udp.destination_port == 7000
    assert parsed.udp.payload == b"relay-ping"
    assert classify_probe_evidence(parsed) is ProbeOutcome.OPEN


def test_icmp_port_unreachable_means_refused() -> None:
    packet = build_ipv4_icmp_packet(icmp_type=3, code=3, payload=b"relay")

    assert classify_probe_evidence(parse_network_packet(packet)) is ProbeOutcome.REFUSED


def test_icmp_admin_prohibited_means_filtered_or_unreachable() -> None:
    packet = build_ipv4_icmp_packet(icmp_type=3, code=13, payload=b"relay")

    assert (
        classify_probe_evidence(parse_probe_packet(PacketLike(packet)))
        is ProbeOutcome.FILTERED_OR_UNREACHABLE
    )


def test_timeout_stays_distinct_from_filtered_evidence() -> None:
    assert classify_probe_evidence(TimeoutEvidence(seconds=0.5)) is ProbeOutcome.TIMEOUT


@pytest.mark.parametrize(
    "task_id, definition",
    [("", "diagnose-relay"), ("task-09", "   ")],
)
def test_relay_task_validates_required_fields(task_id: str, definition: str) -> None:
    with pytest.raises(ValueError):
        RelayTask(task_id, definition, TaskState.RUNNING)


def test_version_is_exposed() -> None:
    assert __version__
