"""Functional tests for sending relay job frames over a loopback TCP socket.

These are functional tests. They drive the public framing interface of
``lab_16_framed_protocol`` across a real TCP connection on 127.0.0.1 with an
operating-system assigned port: ``send_frame`` writes job frames to the
client socket, and an ``IncrementalFrameReader`` reassembles them from the
chunks that ``recv`` returns on the accepted server socket. No network
beyond loopback is used and every socket is closed by the fixture.
"""

# pyright: strict

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

import lab_16_framed_protocol as lab

TIMEOUT_SECONDS = 5.0


@pytest.fixture
def connection() -> Iterator[tuple[socket.socket, socket.socket]]:
    """Yield a connected (client, server) pair on loopback and close both afterward."""
    with socket.create_server(("127.0.0.1", 0)) as listener:
        client = socket.create_connection(listener.getsockname()[:2], timeout=TIMEOUT_SECONDS)
        server, _ = listener.accept()
    server.settimeout(TIMEOUT_SECONDS)
    with client, server:
        yield client, server


def receive_frames(
    server: socket.socket, reader: lab.IncrementalFrameReader, chunk_size: int
) -> list[bytes]:
    """Read until the peer closes, feeding each received chunk to the reader."""
    frames: list[bytes] = []
    while chunk := server.recv(chunk_size):
        frames.extend(reader.feed(chunk))
    frames.extend(reader.finish())
    return frames


def test_job_status_frames_arrive_intact_over_loopback(
    connection: tuple[socket.socket, socket.socket],
) -> None:
    client, server = connection
    jobs = [
        lab.RelayTask("task-17", "resize-image", lab.TaskState.QUEUED),
        lab.RelayTask("task-17", "resize-image", lab.TaskState.RUNNING),
        lab.RelayTask("task-17", "resize-image", lab.TaskState.SUCCEEDED),
        lab.RelayTask("task-18", "archive-logs", lab.TaskState.FAILED),
    ]

    for job in jobs:
        sent = lab.send_frame(client, job.to_payload())
        assert sent == lab.HEADER_SIZE + len(job.to_payload())
    client.shutdown(socket.SHUT_WR)

    payloads = receive_frames(server, lab.IncrementalFrameReader(), chunk_size=3)

    assert [lab.RelayTask.from_payload(payload) for payload in payloads] == jobs


def test_peer_closing_mid_frame_is_reported_as_premature_end(
    connection: tuple[socket.socket, socket.socket],
) -> None:
    client, server = connection
    frame = lab.render_task_frame(lab.RelayTask("task-17", "resize-image"))
    client.sendall(frame[:-2])
    client.shutdown(socket.SHUT_WR)

    with pytest.raises(lab.UnexpectedEOFError, match="mid-frame"):
        receive_frames(server, lab.IncrementalFrameReader(), chunk_size=5)


def test_oversized_frame_from_peer_is_rejected_before_its_payload_is_buffered(
    connection: tuple[socket.socket, socket.socket],
) -> None:
    client, server = connection
    lab.send_frame(client, b"x" * 64)
    client.shutdown(socket.SHUT_WR)
    reader = lab.IncrementalFrameReader(maximum_frame_size=16)

    with pytest.raises(lab.FrameTooLargeError, match="maximum size"):
        receive_frames(server, reader, chunk_size=lab.HEADER_SIZE)
