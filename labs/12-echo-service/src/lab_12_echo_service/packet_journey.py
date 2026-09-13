"""Deterministic Scapy model of one chat message crossing the TCP/IP stack."""

from __future__ import annotations

import argparse
import ipaddress
import re
import textwrap
from dataclasses import dataclass
from typing import Literal

from scapy.layers.inet import IP, TCP
from scapy.layers.l2 import Ether
from scapy.packet import Raw

_MAC_ADDRESS = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
_MAX_MESSAGE_BYTES = 1024

TraceView = Literal["encapsulation", "decapsulation", "combined"]


@dataclass(frozen=True, slots=True)
class PacketEndpoint:
    """Addresses used by one side of the offline chat exchange."""

    mac: str
    ip: str
    port: int

    def __post_init__(self) -> None:
        if _MAC_ADDRESS.fullmatch(self.mac) is None:
            raise ValueError("mac must contain six hexadecimal octets")
        try:
            parsed_ip = ipaddress.ip_address(self.ip)
        except ValueError as exc:
            raise ValueError("ip must be a valid IPv4 address") from exc
        if parsed_ip.version != 4:
            raise ValueError("this lab trace requires an IPv4 address")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class LayerSnapshot:
    """One protocol data unit before adding or after removing a header."""

    phase: Literal["encapsulation", "decapsulation"]
    layer: str
    unit: str
    total_bytes: int
    header_bytes: int
    summary: str
    data_hex: str

    def render(self) -> str:
        prefix = (
            f"{self.layer:11} {self.unit:16} total={self.total_bytes:4} "
            f"header={self.header_bytes:2}  {self.summary}"
        )
        hex_lines = "\n".join(f"{'':30}{line}" for line in textwrap.wrap(self.data_hex, width=47))
        return f"{prefix}\n{hex_lines}"


@dataclass(frozen=True, slots=True)
class PacketJourney:
    """Transmit and receive views of the same serialized Ethernet frame."""

    message: str
    payload: bytes
    frame: bytes
    encapsulation: tuple[LayerSnapshot, ...]
    decapsulation: tuple[LayerSnapshot, ...]

    def render(self, view: TraceView = "combined") -> str:
        if view == "encapsulation":
            return _render_stage_group("TX ENCAPSULATION (top down)", self.encapsulation)
        if view == "decapsulation":
            return _render_stage_group("RX DECAPSULATION (bottom up)", self.decapsulation)
        if view != "combined":
            raise ValueError("view must be encapsulation, decapsulation or combined")
        return "\n\n".join(
            (
                _render_stage_group("TX ENCAPSULATION (top down)", self.encapsulation),
                f"WIRE Ethernet frame       total={len(self.frame):4}\n"
                f"{_indent_hex(self.frame.hex(' '))}",
                _render_stage_group("RX DECAPSULATION (bottom up)", self.decapsulation),
            )
        )


def trace_chat_message(
    message: str,
    *,
    source: PacketEndpoint | None = None,
    destination: PacketEndpoint | None = None,
    sequence: int = 1,
) -> PacketJourney:
    """Encapsulate and decapsulate one UTF-8 message without network access."""

    payload = message.encode("utf-8")
    if not payload:
        raise ValueError("message must not be empty")
    if len(payload) > _MAX_MESSAGE_BYTES:
        raise ValueError(f"message must not exceed {_MAX_MESSAGE_BYTES} UTF-8 bytes")
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("sequence must fit in an unsigned 32-bit integer")

    sender = source or PacketEndpoint("02:00:00:00:00:01", "192.0.2.10", 41000)
    receiver = destination or PacketEndpoint("02:00:00:00:00:02", "198.51.100.20", 9000)
    packet = (
        Ether(src=sender.mac, dst=receiver.mac)
        / IP(src=sender.ip, dst=receiver.ip, ttl=64)
        / TCP(sport=sender.port, dport=receiver.port, flags="PA", seq=sequence)
        / Raw(load=payload)
    )
    frame = bytes(packet)
    return decode_chat_frame(frame, expected_message=message)


def decode_chat_frame(frame: bytes, *, expected_message: str | None = None) -> PacketJourney:
    """Dissect an Ethernet/IPv4/TCP frame and return both directional views."""

    if len(frame) < 14:
        raise ValueError("frame is too short for an Ethernet header")
    parsed = Ether(frame)
    if not parsed.haslayer(IP):
        raise ValueError("frame does not contain IPv4")
    if not parsed.haslayer(TCP):
        raise ValueError("frame does not contain TCP")

    ip_layer = parsed[IP]
    tcp_layer = parsed[TCP]
    payload = bytes(tcp_layer.payload)
    if not payload:
        raise ValueError("TCP segment does not contain chat data")
    try:
        message = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("chat data must be valid UTF-8") from exc
    if expected_message is not None and message != expected_message:
        raise ValueError("decapsulated chat data does not match the sent message")

    ip_bytes = bytes(ip_layer)
    tcp_bytes = bytes(tcp_layer)
    ip_header_bytes = int(ip_layer.ihl or 5) * 4
    tcp_header_bytes = int(tcp_layer.dataofs or 5) * 4
    source = f"{parsed.src} -> {parsed.dst}"
    route = f"{ip_layer.src}:{tcp_layer.sport} -> {ip_layer.dst}:{tcp_layer.dport}"

    encapsulation = (
        _snapshot("encapsulation", "application", "message", payload, 0, repr(message)),
        _snapshot(
            "encapsulation",
            "transport",
            "TCP segment",
            tcp_bytes,
            tcp_header_bytes,
            f"seq={tcp_layer.seq} flags={tcp_layer.sprintf('%TCP.flags%')}",
        ),
        _snapshot(
            "encapsulation",
            "internet",
            "IPv4 packet",
            ip_bytes,
            ip_header_bytes,
            f"{ip_layer.src} -> {ip_layer.dst} protocol=TCP",
        ),
        _snapshot("encapsulation", "link", "Ethernet frame", frame, 14, source),
    )
    decapsulation = (
        _snapshot("decapsulation", "link", "Ethernet frame", frame, 14, source),
        _snapshot(
            "decapsulation",
            "internet",
            "IPv4 packet",
            ip_bytes,
            ip_header_bytes,
            f"{ip_layer.src} -> {ip_layer.dst} protocol=TCP",
        ),
        _snapshot(
            "decapsulation",
            "transport",
            "TCP segment",
            tcp_bytes,
            tcp_header_bytes,
            route,
        ),
        _snapshot("decapsulation", "application", "message", payload, 0, repr(message)),
    )
    return PacketJourney(message, payload, frame, encapsulation, decapsulation)


def _snapshot(
    phase: Literal["encapsulation", "decapsulation"],
    layer: str,
    unit: str,
    data: bytes,
    header_bytes: int,
    summary: str,
) -> LayerSnapshot:
    return LayerSnapshot(phase, layer, unit, len(data), header_bytes, summary, data.hex(" "))


def _render_stage_group(title: str, snapshots: tuple[LayerSnapshot, ...]) -> str:
    return "\n".join((title, *(snapshot.render() for snapshot in snapshots)))


def _indent_hex(data_hex: str) -> str:
    return "\n".join(f"{'':30}{line}" for line in textwrap.wrap(data_hex, width=47))


def main(argv: list[str] | None = None) -> int:
    """Print an offline packet journey for one user-supplied chat message."""

    parser = argparse.ArgumentParser(
        description="Show a chat message being encapsulated and decapsulated."
    )
    parser.add_argument("message", help="UTF-8 text to place in the TCP payload")
    parser.add_argument(
        "--view",
        choices=("encapsulation", "decapsulation", "combined"),
        default="combined",
    )
    args = parser.parse_args(argv)
    journey = trace_chat_message(args.message)
    print(journey.render(args.view))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
