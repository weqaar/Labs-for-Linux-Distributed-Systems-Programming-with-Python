"""Tests for the lab_11_framed_protocol package."""

from __future__ import annotations

import pytest

from lab_11_framed_protocol import (
    FrameTooLargeError,
    IncrementalFrameReader,
    RelayTask,
    TaskState,
    UnexpectedEOFError,
    __version__,
    decode_frame,
    encode_frame,
    render_task_frame,
    send_frame,
)


class PartialWriter:
    def __init__(self, *, chunk_size: int) -> None:
        self._chunk_size = chunk_size
        self.buffer = bytearray()
        self.calls = 0

    def send(self, data: bytes | bytearray | memoryview, /) -> int:
        self.calls += 1
        chunk = bytes(data[: self._chunk_size])
        self.buffer.extend(chunk)
        return len(chunk)


def test_task_payload_round_trip_survives_framing() -> None:
    task = RelayTask("task-11", "frame-status", TaskState.RUNNING)
    frame = render_task_frame(task)

    decoded = RelayTask.from_payload(decode_frame(frame))

    assert decoded == task


def test_split_frame_across_reads() -> None:
    payload = RelayTask("task-12", "split-frame", TaskState.QUEUED).to_payload()
    frame = encode_frame(payload)
    reader = IncrementalFrameReader()

    assert reader.feed(frame[:3]) == []
    assert reader.feed(frame[3:7]) == []
    assert reader.feed(frame[7:]) == [payload]
    assert reader.finish() == []


def test_multiple_frames_can_arrive_in_one_chunk() -> None:
    first = RelayTask("task-13", "batch-one", TaskState.RUNNING).to_payload()
    second = RelayTask("task-14", "batch-two", TaskState.SUCCEEDED).to_payload()
    reader = IncrementalFrameReader()

    frames = reader.feed(encode_frame(first) + encode_frame(second))

    assert frames == [first, second]


def test_length_larger_than_the_maximum_is_rejected() -> None:
    with pytest.raises(FrameTooLargeError, match="maximum size"):
        encode_frame(b"12345", maximum_frame_size=4)
    with pytest.raises(FrameTooLargeError, match="maximum size"):
        IncrementalFrameReader(maximum_frame_size=4).feed((5).to_bytes(4, "big"))


def test_stream_end_mid_frame_raises() -> None:
    reader = IncrementalFrameReader()
    reader.feed(b"\x00\x00\x00\x05abc")

    with pytest.raises(UnexpectedEOFError, match="mid-frame"):
        reader.finish()


def test_partial_writes_send_the_entire_frame() -> None:
    payload = RelayTask("task-15", "partial-write", TaskState.FAILED).to_payload()
    writer = PartialWriter(chunk_size=3)

    sent = send_frame(writer, payload)

    assert sent == len(encode_frame(payload))
    assert bytes(writer.buffer) == encode_frame(payload)
    assert writer.calls > 1


def test_reader_reuse_for_blob_chunks() -> None:
    first = RelayTask("task-16", "blob-one", TaskState.QUEUED).to_payload()
    second = RelayTask("task-17", "blob-two", TaskState.SUCCEEDED).to_payload()
    first_frame = encode_frame(first)
    second_frame = encode_frame(second)
    chunks = [
        first_frame[:2],
        first_frame[2:5],
        first_frame[5:] + second_frame[:3],
        second_frame[3:],
    ]
    reader = IncrementalFrameReader()

    frames = reader.read_chunks(chunks)

    assert frames == [first, second]


@pytest.mark.parametrize(
    ("task_id", "definition"),
    [("", "frame"), ("task-18", "   ")],
)
def test_relay_task_validates_required_fields(task_id: str, definition: str) -> None:
    with pytest.raises(ValueError):
        RelayTask(task_id, definition)


def test_version_is_exposed() -> None:
    assert __version__
