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


class BufferFullError(FrameError):
    """Raised when a write would exceed a bounded ring buffer."""


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


class BoundedRingBuffer:
    """Fixed-capacity byte FIFO whose unread region can wrap once."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._storage = bytearray(capacity)
        self._head = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    @property
    def capacity(self) -> int:
        return len(self._storage)

    @property
    def free(self) -> int:
        return self.capacity - self._size

    def write(self, data: ReadableBuffer) -> None:
        raw = _coerce_bytes(data)
        if len(raw) > self.free:
            raise BufferFullError(f"write of {len(raw)} bytes exceeds {self.free} bytes free")
        tail = (self._head + self._size) % self.capacity
        first = min(len(raw), self.capacity - tail)
        self._storage[tail : tail + first] = raw[:first]
        self._storage[: len(raw) - first] = raw[first:]
        self._size += len(raw)

    def peek(self, size: int, *, offset: int = 0) -> bytes:
        if size < 0 or offset < 0:
            raise ValueError("size and offset must not be negative")
        if offset + size > self._size:
            raise UnexpectedEOFError("ring buffer does not contain requested bytes")
        start = (self._head + offset) % self.capacity
        first = min(size, self.capacity - start)
        return bytes(self._storage[start : start + first] + self._storage[: size - first])

    def read(self, size: int) -> bytes:
        data = self.peek(size)
        self._head = (self._head + size) % self.capacity
        self._size -= size
        if self._size == 0:
            self._head = 0
        return data


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

    def __init__(
        self,
        *,
        maximum_frame_size: int = MAX_FRAME_SIZE,
        buffer_capacity: int | None = None,
    ) -> None:
        minimum_capacity = HEADER_SIZE + maximum_frame_size
        actual_capacity = buffer_capacity or minimum_capacity
        if actual_capacity < minimum_capacity:
            raise ValueError("buffer capacity must hold one header and maximum-sized payload")
        self._maximum_frame_size = maximum_frame_size
        self._buffer = BoundedRingBuffer(actual_capacity)

    def feed(self, chunk: ReadableBuffer) -> list[bytes]:
        frames: list[bytes] = []
        remaining = memoryview(_coerce_bytes(chunk))
        while remaining:
            if self._buffer.free == 0:
                drained = self._drain_frames()
                if not drained:
                    raise BufferFullError("frame reader made no buffer progress")
                frames.extend(drained)
            accepted = min(len(remaining), self._buffer.free)
            self._buffer.write(remaining[:accepted])
            remaining = remaining[accepted:]
            frames.extend(self._drain_frames())
        return frames

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
        while len(self._buffer) >= HEADER_SIZE:
            header = _parse_header(
                self._buffer.peek(HEADER_SIZE),
                maximum_frame_size=self._maximum_frame_size,
            )
            frame_size = HEADER_SIZE + header.payload_length
            if len(self._buffer) < frame_size:
                break
            self._buffer.read(HEADER_SIZE)
            frames.append(self._buffer.read(header.payload_length))
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
