"""Packet parsing and probe planning for relay diagnostics."""

from __future__ import annotations

from .diagnostics import (
    ProbeOutcome,
    ProbePlan,
    ProbeProtocol,
    TimeoutEvidence,
    build_relay_probe_plan,
    classify_probe_evidence,
    parse_probe_packet,
)
from .models import RelayTask, TaskState
from .packets import (
    IcmpMessage,
    IPv4Header,
    PacketFormatError,
    ParsedPacket,
    TcpFlag,
    TcpSegment,
    UdpDatagram,
    build_ipv4_icmp_packet,
    build_ipv4_tcp_packet,
    build_ipv4_udp_packet,
    parse_ipv4_packet,
    parse_network_packet,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "IcmpMessage",
    "IPv4Header",
    "PacketFormatError",
    "ParsedPacket",
    "ProbeOutcome",
    "ProbePlan",
    "ProbeProtocol",
    "RelayTask",
    "TaskState",
    "TcpFlag",
    "TcpSegment",
    "TimeoutEvidence",
    "UdpDatagram",
    "build_ipv4_icmp_packet",
    "build_ipv4_tcp_packet",
    "build_ipv4_udp_packet",
    "build_relay_probe_plan",
    "classify_probe_evidence",
    "parse_ipv4_packet",
    "parse_network_packet",
    "parse_probe_packet",
]
