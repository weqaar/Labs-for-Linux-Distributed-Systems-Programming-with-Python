"""Tests for the lab_12_echo_service package."""

from __future__ import annotations

import socket

import pytest

from lab_12_echo_service import (
    DatagramEchoClient,
    DatagramEchoServer,
    EchoClient,
    EchoServer,
    RelayTask,
    TaskState,
    __version__,
)


def test_task_wire_round_trip_preserves_task_fields() -> None:
    task = RelayTask("task-08", "echo-health", TaskState.RUNNING)

    decoded = RelayTask.from_wire(task.to_wire())

    assert decoded == task


@pytest.mark.parametrize(
    "payload",
    [b"task-08", b"bad|payload", b"task|definition|missing-state"],
)
def test_wire_parser_rejects_invalid_payloads(payload: bytes) -> None:
    with pytest.raises(ValueError):
        RelayTask.from_wire(payload)


def test_echo_server_round_trips_bytes_over_loopback() -> None:
    server = EchoServer(read_size=32, socket_timeout=0.1)
    server.start()
    task = RelayTask("task-09", "sync-status", TaskState.QUEUED)

    try:
        with EchoClient(*server.address, socket_timeout=0.1) as client:
            wire = task.to_wire()
            client.send(wire)
            echoed = client.receive_exactly(len(wire))
        assert echoed == wire
    finally:
        server.close()


def test_reads_do_not_preserve_write_boundaries() -> None:
    server = EchoServer(read_size=4, socket_timeout=0.1)
    server.start()
    task = RelayTask("task-10", "stream-proof", TaskState.SUCCEEDED)
    wire = task.to_wire()

    try:
        with EchoClient(*server.address, socket_timeout=0.1) as client:
            client.send(wire)
            client.shutdown_write()
            assert server.wait_for_reads(3)
            echoed = client.receive_exactly(len(wire))
        observed = server.observed_reads
        assert echoed == wire
        assert len(observed) >= 3
        assert b"".join(observed) == wire
        assert all(len(chunk) <= 4 for chunk in observed)
    finally:
        server.close()


def test_server_shutdown_is_clean_and_stops_accepting_connections() -> None:
    server = EchoServer(read_size=16, socket_timeout=0.1)
    server.start()

    with EchoClient(*server.address, socket_timeout=0.1) as client:
        client.send(RelayTask("task-11", "shutdown-check").to_wire())
        server.close()

    assert not server.is_running()
    with pytest.raises(OSError):
        socket.create_connection(server.address, timeout=0.1)


def test_udp_echo_preserves_one_datagram_boundary() -> None:
    server = DatagramEchoServer(socket_timeout=0.05)
    server.start()

    try:
        with DatagramEchoClient(*server.address, socket_timeout=0.1) as client:
            exchange = client.exchange(b"Hello SigRaft")
    finally:
        server.close()

    assert exchange.response == b"Hello SigRaft"
    assert exchange.attempts == 1
    assert server.received_datagrams == [b"Hello SigRaft"]
    assert not server.is_running()


def test_udp_reliability_requires_an_application_retry_policy() -> None:
    server = DatagramEchoServer(socket_timeout=0.02, drop_first=True)
    server.start()

    try:
        with DatagramEchoClient(*server.address, socket_timeout=0.5) as client:
            exchange = client.exchange(b"Hello SigRaft", max_attempts=2)
    finally:
        server.close()

    assert exchange.response == b"Hello SigRaft"
    assert exchange.attempts == 2
    assert server.received_datagrams == [b"Hello SigRaft", b"Hello SigRaft"]
    assert not server.is_running()


def test_udp_client_rejects_invalid_retry_arguments() -> None:
    server = DatagramEchoServer()
    server.start()

    try:
        with DatagramEchoClient(*server.address) as client:
            with pytest.raises(ValueError, match="must not be empty"):
                client.exchange(b"")
            with pytest.raises(ValueError, match="must be positive"):
                client.exchange(b"Hello SigRaft", max_attempts=0)
    finally:
        server.close()


def test_version_is_exposed() -> None:
    assert __version__
