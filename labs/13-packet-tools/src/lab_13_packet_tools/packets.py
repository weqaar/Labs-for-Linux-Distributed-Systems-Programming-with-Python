"""Offline packet builders and parsers for tests and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
from ipaddress import IPv4Address
from typing import SupportsBytes


class PacketFormatError(ValueError):
    """Raised when a packet is truncated or malformed."""


class TcpFlag(IntFlag):
    """Subset of TCP flags used by the diagnostics checkpoint."""

    FIN = 0x01
    SYN = 0x02
    RST = 0x04
    PSH = 0x08
    ACK = 0x10


@dataclass(frozen=True)
class IPv4Header:
    version: int
    header_length: int
    total_length: int
    protocol: int
    ttl: int
    source: str
    destination: str


@dataclass(frozen=True)
class TcpSegment:
    source_port: int
    destination_port: int
    flags: TcpFlag
    payload: bytes


@dataclass(frozen=True)
class UdpDatagram:
    source_port: int
    destination_port: int
    payload: bytes


@dataclass(frozen=True)
class IcmpMessage:
    icmp_type: int
    code: int
    payload: bytes


@dataclass(frozen=True)
class ParsedPacket:
    header: IPv4Header
    tcp: TcpSegment | None = None
    udp: UdpDatagram | None = None
    icmp: IcmpMessage | None = None


def parse_ipv4_packet(
    packet: bytes | bytearray | memoryview | SupportsBytes,
) -> tuple[IPv4Header, bytes]:
    data = _coerce_bytes(packet)
    if len(data) < 20:
        raise PacketFormatError("IPv4 header requires at least 20 bytes")
    version = data[0] >> 4
    if version != 4:
        raise PacketFormatError("only IPv4 packets are supported")
    header_length = (data[0] & 0x0F) * 4
    if header_length < 20 or len(data) < header_length:
        raise PacketFormatError("IPv4 header is truncated")
    total_length = int.from_bytes(data[2:4], "big")
    if total_length < header_length or total_length > len(data):
        raise PacketFormatError("IPv4 total length is invalid")
    header = IPv4Header(
        version=version,
        header_length=header_length,
        total_length=total_length,
        protocol=data[9],
        ttl=data[8],
        source=str(IPv4Address(data[12:16])),
        destination=str(IPv4Address(data[16:20])),
    )
    return header, data[header_length:total_length]


def parse_network_packet(packet: bytes | bytearray | memoryview | SupportsBytes) -> ParsedPacket:
    header, payload = parse_ipv4_packet(packet)
    if header.protocol == 6:
        return ParsedPacket(header=header, tcp=_parse_tcp_segment(payload))
    if header.protocol == 17:
        return ParsedPacket(header=header, udp=_parse_udp_datagram(payload))
    if header.protocol == 1:
        return ParsedPacket(header=header, icmp=_parse_icmp_message(payload))
    raise PacketFormatError(f"unsupported IPv4 protocol: {header.protocol}")


def build_ipv4_tcp_packet(
    *,
    source_port: int,
    destination_port: int,
    flags: TcpFlag,
    payload: bytes = b"",
    source: str = "10.0.0.1",
    destination: str = "10.0.0.2",
) -> bytes:
    tcp_header = b"".join(
        [
            source_port.to_bytes(2, "big"),
            destination_port.to_bytes(2, "big"),
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            bytes([(5 << 4), int(flags)]),
            (0).to_bytes(2, "big"),
            (0).to_bytes(2, "big"),
            (0).to_bytes(2, "big"),
        ]
    )
    return (
        _ipv4_header(6, len(tcp_header) + len(payload), source, destination) + tcp_header + payload
    )


def build_ipv4_udp_packet(
    *,
    source_port: int,
    destination_port: int,
    payload: bytes = b"",
    source: str = "10.0.0.1",
    destination: str = "10.0.0.2",
) -> bytes:
    length = 8 + len(payload)
    udp_header = b"".join(
        [
            source_port.to_bytes(2, "big"),
            destination_port.to_bytes(2, "big"),
            length.to_bytes(2, "big"),
            (0).to_bytes(2, "big"),
        ]
    )
    return _ipv4_header(17, length, source, destination) + udp_header + payload


def build_ipv4_icmp_packet(
    *,
    icmp_type: int,
    code: int,
    payload: bytes = b"",
    source: str = "10.0.0.1",
    destination: str = "10.0.0.2",
) -> bytes:
    icmp_header = bytes([icmp_type, code, 0, 0])
    return (
        _ipv4_header(1, len(icmp_header) + len(payload), source, destination)
        + icmp_header
        + payload
    )


def _parse_tcp_segment(segment: bytes) -> TcpSegment:
    if len(segment) < 20:
        raise PacketFormatError("TCP segment requires at least 20 bytes")
    data_offset = (segment[12] >> 4) * 4
    if data_offset < 20 or len(segment) < data_offset:
        raise PacketFormatError("TCP segment is truncated")
    return TcpSegment(
        source_port=int.from_bytes(segment[0:2], "big"),
        destination_port=int.from_bytes(segment[2:4], "big"),
        flags=TcpFlag(segment[13]),
        payload=segment[data_offset:],
    )


def _parse_udp_datagram(datagram: bytes) -> UdpDatagram:
    if len(datagram) < 8:
        raise PacketFormatError("UDP datagram requires at least 8 bytes")
    length = int.from_bytes(datagram[4:6], "big")
    if length < 8 or len(datagram) < length:
        raise PacketFormatError("UDP datagram is truncated")
    return UdpDatagram(
        source_port=int.from_bytes(datagram[0:2], "big"),
        destination_port=int.from_bytes(datagram[2:4], "big"),
        payload=datagram[8:length],
    )


def _parse_icmp_message(message: bytes) -> IcmpMessage:
    if len(message) < 4:
        raise PacketFormatError("ICMP message requires at least 4 bytes")
    return IcmpMessage(icmp_type=message[0], code=message[1], payload=message[4:])


def _ipv4_header(protocol: int, payload_length: int, source: str, destination: str) -> bytes:
    total_length = 20 + payload_length
    return b"".join(
        [
            bytes([(4 << 4) | 5, 0]),
            total_length.to_bytes(2, "big"),
            (0).to_bytes(2, "big"),
            (0).to_bytes(2, "big"),
            bytes([64, protocol]),
            (0).to_bytes(2, "big"),
            IPv4Address(source).packed,
            IPv4Address(destination).packed,
        ]
    )


def _coerce_bytes(packet: bytes | bytearray | memoryview | SupportsBytes) -> bytes:
    if isinstance(packet, bytes):
        return packet
    if isinstance(packet, bytearray):
        return bytes(packet)
    if isinstance(packet, memoryview):
        return packet.tobytes()
    try:
        return bytes(packet)
    except TypeError as exc:
        raise PacketFormatError("packet must be bytes-like") from exc
