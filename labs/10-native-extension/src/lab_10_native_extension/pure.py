"""Pure-Python framing helpers used as the reference implementation."""

from __future__ import annotations

HEADER_SIZE = 4
MAX_FRAME_SIZE = 1024 * 1024
ReadableBuffer = bytes | bytearray | memoryview


def encode_frame(payload: ReadableBuffer, *, maximum_frame_size: int = MAX_FRAME_SIZE) -> bytes:
    raw = coerce_bytes(payload)
    if len(raw) > maximum_frame_size:
        raise ValueError(f"frame exceeds maximum size of {maximum_frame_size} bytes")
    return len(raw).to_bytes(HEADER_SIZE, "big") + raw


def parse_length_prefix(
    frame: ReadableBuffer,
    *,
    maximum_frame_size: int = MAX_FRAME_SIZE,
) -> int:
    raw = coerce_bytes(frame)
    if len(raw) < HEADER_SIZE:
        raise ValueError("frame header requires 4 bytes")
    payload_length = int.from_bytes(raw[:HEADER_SIZE], "big")
    if payload_length > maximum_frame_size:
        raise ValueError(f"frame exceeds maximum size of {maximum_frame_size} bytes")
    return payload_length


def fletcher16(payload: ReadableBuffer) -> int:
    raw = coerce_bytes(payload)
    sum1 = 0
    sum2 = 0
    for byte in raw:
        sum1 = (sum1 + byte) % 255
        sum2 = (sum2 + sum1) % 255
    return (sum2 << 8) | sum1


def coerce_bytes(buffer: ReadableBuffer) -> bytes:
    if isinstance(buffer, bytes):
        return buffer
    if isinstance(buffer, bytearray):
        return bytes(buffer)
    return buffer.tobytes()
