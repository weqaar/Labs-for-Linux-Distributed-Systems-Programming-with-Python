"""Probe planning and evidence classification for relay diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import SupportsBytes

from .models import RelayTask
from .packets import IcmpMessage, ParsedPacket, TcpFlag, parse_network_packet


class ProbeProtocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class ProbeOutcome(str, Enum):
    OPEN = "open"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    FILTERED_OR_UNREACHABLE = "filtered-or-unreachable"


@dataclass(frozen=True)
class ProbePlan:
    name: str
    task: RelayTask
    host: str
    port: int
    protocol: ProbeProtocol
    timeout_seconds: float
    expected_outcome: ProbeOutcome
    reason: str


@dataclass(frozen=True)
class TimeoutEvidence:
    seconds: float


def build_relay_probe_plan(
    task: RelayTask,
    *,
    host: str,
    service_port: int,
    timeout_seconds: float = 0.5,
) -> tuple[ProbePlan, ProbePlan, ProbePlan]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return (
        ProbePlan(
            name="open-service-port",
            task=task,
            host=host,
            port=service_port,
            protocol=ProbeProtocol.TCP,
            timeout_seconds=timeout_seconds,
            expected_outcome=ProbeOutcome.OPEN,
            reason="Expect a SYN-ACK when relay is reachable on its service port.",
        ),
        ProbePlan(
            name="closed-reference-port",
            task=task,
            host=host,
            port=service_port + 1,
            protocol=ProbeProtocol.TCP,
            timeout_seconds=timeout_seconds,
            expected_outcome=ProbeOutcome.REFUSED,
            reason=(
                "A reset or ICMP port unreachable proves the host is reachable "
                "but the port is closed."
            ),
        ),
        ProbePlan(
            name="filtered-or-silent-drop",
            task=task,
            host=host,
            port=service_port,
            protocol=ProbeProtocol.UDP,
            timeout_seconds=timeout_seconds,
            expected_outcome=ProbeOutcome.TIMEOUT,
            reason=(
                "No reply shows a silent drop, while ICMP administratively "
                "prohibited is stronger filtered evidence."
            ),
        ),
    )


def parse_probe_packet(
    packet: bytes | bytearray | memoryview | SupportsBytes,
) -> ParsedPacket:
    return parse_network_packet(packet)


def classify_probe_evidence(evidence: ParsedPacket | TimeoutEvidence) -> ProbeOutcome:
    if isinstance(evidence, TimeoutEvidence):
        return ProbeOutcome.TIMEOUT
    if evidence.tcp is not None:
        flags = evidence.tcp.flags
        if flags & TcpFlag.RST:
            return ProbeOutcome.REFUSED
        if flags & TcpFlag.SYN and flags & TcpFlag.ACK:
            return ProbeOutcome.OPEN
        return ProbeOutcome.FILTERED_OR_UNREACHABLE
    if evidence.icmp is not None:
        return _classify_icmp(evidence.icmp)
    if evidence.udp is not None:
        return ProbeOutcome.OPEN
    return ProbeOutcome.FILTERED_OR_UNREACHABLE


def _classify_icmp(message: IcmpMessage) -> ProbeOutcome:
    if message.icmp_type == 3 and message.code == 3:
        return ProbeOutcome.REFUSED
    if message.icmp_type == 3 and message.code in {0, 1, 2, 9, 10, 13}:
        return ProbeOutcome.FILTERED_OR_UNREACHABLE
    return ProbeOutcome.FILTERED_OR_UNREACHABLE
