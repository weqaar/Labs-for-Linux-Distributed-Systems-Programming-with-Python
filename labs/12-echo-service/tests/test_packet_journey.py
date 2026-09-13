"""Tests for the offline Scapy packet journey."""

from __future__ import annotations

import pytest
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw

from lab_12_echo_service import (
    EchoClient,
    EchoServer,
    PacketEndpoint,
    decode_chat_frame,
    trace_chat_message,
)
from lab_12_echo_service.packet_journey import main


def test_chat_message_is_encapsulated_and_decapsulated_in_layer_order() -> None:
    source = PacketEndpoint("02:00:00:00:00:0a", "192.0.2.10", 41000)
    destination = PacketEndpoint("02:00:00:00:00:0b", "198.51.100.20", 9000)

    journey = trace_chat_message(
        "Hello, distributed systems!",
        source=source,
        destination=destination,
        sequence=17,
    )
    packet = Ether(journey.frame)

    assert [stage.layer for stage in journey.encapsulation] == [
        "application",
        "transport",
        "internet",
        "link",
    ]
    assert [stage.layer for stage in journey.decapsulation] == [
        "link",
        "internet",
        "transport",
        "application",
    ]
    assert packet.src == source.mac
    assert packet.dst == destination.mac
    assert packet[IP].src == source.ip
    assert packet[IP].dst == destination.ip
    assert packet[TCP].sport == source.port
    assert packet[TCP].dport == destination.port
    assert packet[TCP].seq == 17
    assert packet[TCP].chksum is not None
    assert bytes(packet[Raw]) == journey.payload
    assert journey.decapsulation[-1].summary == repr(journey.message)


def test_layer_sizes_show_each_header_being_added_and_removed() -> None:
    journey = trace_chat_message("size every layer")
    application, transport, internet, link = journey.encapsulation

    assert application.total_bytes == len(journey.payload)
    assert transport.header_bytes == 20
    assert transport.total_bytes == application.total_bytes + transport.header_bytes
    assert internet.header_bytes == 20
    assert internet.total_bytes == transport.total_bytes + internet.header_bytes
    assert link.header_bytes == 14
    assert link.total_bytes == internet.total_bytes + link.header_bytes
    assert [stage.total_bytes for stage in journey.decapsulation] == [
        link.total_bytes,
        internet.total_bytes,
        transport.total_bytes,
        application.total_bytes,
    ]


def test_trace_views_show_separate_and_combined_directions() -> None:
    journey = trace_chat_message("show both ends")

    transmit = journey.render("encapsulation")
    receive = journey.render("decapsulation")
    combined = journey.render()

    assert "TX ENCAPSULATION (top down)" in transmit
    assert "RX DECAPSULATION" not in transmit
    assert "RX DECAPSULATION (bottom up)" in receive
    assert "TX ENCAPSULATION" not in receive
    assert "WIRE Ethernet frame" in combined
    assert transmit in combined
    assert receive in combined
    assert journey.payload.hex(" ") in combined
    with pytest.raises(ValueError, match="view must be"):
        journey.render("sideways")  # type: ignore[arg-type]


def test_echoed_application_bytes_match_the_packet_trace() -> None:
    server = EchoServer(read_size=7, socket_timeout=0.1)
    server.start()
    journey = trace_chat_message("chat over the relay echo service")

    try:
        with EchoClient(*server.address, socket_timeout=0.1) as client:
            client.send(journey.payload)
            echoed = client.receive_exactly(len(journey.payload))
        received = decode_chat_frame(journey.frame, expected_message=echoed.decode())
    finally:
        server.close()

    assert echoed == journey.payload
    assert received.message == journey.message


@pytest.mark.parametrize(
    ("mac", "ip", "port", "message"),
    [
        ("bad", "192.0.2.1", 9000, "mac must contain"),
        ("02:00:00:00:00:01", "not-an-ip", 9000, "valid IPv4"),
        ("02:00:00:00:00:01", "2001:db8::1", 9000, "requires an IPv4"),
        ("02:00:00:00:00:01", "192.0.2.1", 0, "between 1 and 65535"),
    ],
)
def test_endpoint_rejects_invalid_addresses(mac: str, ip: str, port: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        PacketEndpoint(mac, ip, port)


@pytest.mark.parametrize(
    ("message", "sequence", "error"),
    [
        ("", 1, "must not be empty"),
        ("x" * 1025, 1, "must not exceed"),
        ("hello", -1, "unsigned 32-bit"),
        ("hello", 0x1_0000_0000, "unsigned 32-bit"),
    ],
)
def test_trace_rejects_unbounded_messages_and_sequences(
    message: str, sequence: int, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        trace_chat_message(message, sequence=sequence)


def test_decode_rejects_unsupported_or_malformed_frames() -> None:
    with pytest.raises(ValueError, match="too short"):
        decode_chat_frame(b"short")

    arp_like = bytes(
        Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02", type=0x0806) / Raw(load=b"x" * 60)
    )
    with pytest.raises(ValueError, match="does not contain IPv4"):
        decode_chat_frame(arp_like)

    udp = bytes(
        Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")
        / IP(src="192.0.2.1", dst="198.51.100.1")
        / UDP(sport=41000, dport=9000)
        / Raw(load=b"chat")
    )
    with pytest.raises(ValueError, match="does not contain TCP"):
        decode_chat_frame(udp)

    empty_tcp = bytes(
        Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")
        / IP(src="192.0.2.1", dst="198.51.100.1")
        / TCP(sport=41000, dport=9000)
    )
    with pytest.raises(ValueError, match="does not contain chat data"):
        decode_chat_frame(empty_tcp)


def test_decode_rejects_invalid_utf8_and_unexpected_message() -> None:
    invalid_utf8 = bytes(
        Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")
        / IP(src="192.0.2.1", dst="198.51.100.1")
        / TCP(sport=41000, dport=9000)
        / Raw(load=b"\xff")
    )
    with pytest.raises(ValueError, match="valid UTF-8"):
        decode_chat_frame(invalid_utf8)

    journey = trace_chat_message("sent")
    with pytest.raises(ValueError, match="does not match"):
        decode_chat_frame(journey.frame, expected_message="received")


def test_cli_prints_requested_view(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reader supplied text", "--view", "decapsulation"]) == 0

    output = capsys.readouterr().out
    assert "RX DECAPSULATION (bottom up)" in output
    assert "'reader supplied text'" in output
    assert "TX ENCAPSULATION" not in output
