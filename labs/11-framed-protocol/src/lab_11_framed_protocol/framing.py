"""Incremental 4-byte big-endian length-prefixed framing."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .models import RelayTask

HEADER_SIZE = 4
MAX_FRAME_SIZE = 1024 * 1024
ReadableBuffer = bytes | bytearray | memoryview


class FrameError(ValueError):
    """Base exception for framing failures."""


class FrameTooLargeError(FrameError):
    """Raised when a frame length exceeds the declared maximum."""


class UnexpectedEOFError(FrameError):
    """Raised when the stream ends before the full frame arrives."""


class SupportsSend(Protocol):
    """Writer protocol compatible with socket.send."""

    def send(self, data: bytes | bytearray | memoryview, /) -> int:
        """Write part of the provided frame and return the byte count."""
        ...


@dataclass(frozen=True)
class _FrameHeader:
    payload_length: int


def encode_frame(payload: ReadableBuffer, *, maximum_frame_size: int = MAX_FRAME_SIZE) -> bytes:
    raw = _coerce_bytes(payload)
    if len(raw) > maximum_frame_size:
        raise FrameTooLargeError(f"frame exceeds maximum size of {maximum_frame_size} bytes")
    return len(raw).to_bytes(HEADER_SIZE, "big") + raw


def decode_frame(frame: ReadableBuffer, *, maximum_frame_size: int = MAX_FRAME_SIZE) -> bytes:
    raw = _coerce_bytes(frame)
    header = _parse_header(raw, maximum_frame_size=maximum_frame_size)
    payload = raw[HEADER_SIZE:]
    if len(payload) != header.payload_length:
        raise UnexpectedEOFError("stream ended mid-frame")
    return payload


def render_task_frame(task: RelayTask, *, maximum_frame_size: int = MAX_FRAME_SIZE) -> bytes:
    return encode_frame(task.to_payload(), maximum_frame_size=maximum_frame_size)


def send_frame(
    writer: SupportsSend,
    payload: ReadableBuffer,
    *,
    maximum_frame_size: int = MAX_FRAME_SIZE,
) -> int:
    frame = memoryview(encode_frame(payload, maximum_frame_size=maximum_frame_size))
    total_sent = 0
    while frame:
        sent = writer.send(frame)
        if sent <= 0:
            raise ConnectionError("writer made no forward progress")
        total_sent += sent
        frame = frame[sent:]
    return total_sent


class IncrementalFrameReader:
    """Read frames from arbitrary socket or blob chunks."""

    def __init__(self, *, maximum_frame_size: int = MAX_FRAME_SIZE) -> None:
        self._maximum_frame_size = maximum_frame_size
        self._buffer = bytearray()

    def feed(self, chunk: ReadableBuffer) -> list[bytes]:
        if chunk:
            self._buffer.extend(_coerce_bytes(chunk))
        return self._drain_frames()

    def finish(self) -> list[bytes]:
        frames = self._drain_frames()
        if self._buffer:
            raise UnexpectedEOFError("stream ended mid-frame")
        return frames

    def read_chunks(self, chunks: Iterable[ReadableBuffer]) -> list[bytes]:
        frames: list[bytes] = []
        for chunk in chunks:
            frames.extend(self.feed(chunk))
        frames.extend(self.finish())
        return frames

    def _drain_frames(self) -> list[bytes]:
        frames: list[bytes] = []
        offset = 0
        while True:
            remaining = len(self._buffer) - offset
            if remaining < HEADER_SIZE:
                break
            header = _parse_header(
                memoryview(self._buffer)[offset:],
                maximum_frame_size=self._maximum_frame_size,
            )
            frame_end = offset + HEADER_SIZE + header.payload_length
            if len(self._buffer) < frame_end:
                break
            frames.append(bytes(self._buffer[offset + HEADER_SIZE : frame_end]))
            offset = frame_end
        if offset:
            del self._buffer[:offset]
        return frames


def _parse_header(frame: ReadableBuffer, *, maximum_frame_size: int) -> _FrameHeader:
    raw = _coerce_bytes(frame)
    if len(raw) < HEADER_SIZE:
        raise UnexpectedEOFError("frame header requires 4 bytes")
    payload_length = int.from_bytes(raw[:HEADER_SIZE], "big")
    if payload_length > maximum_frame_size:
        raise FrameTooLargeError(f"frame exceeds maximum size of {maximum_frame_size} bytes")
    return _FrameHeader(payload_length=payload_length)


def _coerce_bytes(buffer: ReadableBuffer) -> bytes:
    if isinstance(buffer, bytes):
        return buffer
    if isinstance(buffer, bytearray):
        return bytes(buffer)
    return buffer.tobytes()
