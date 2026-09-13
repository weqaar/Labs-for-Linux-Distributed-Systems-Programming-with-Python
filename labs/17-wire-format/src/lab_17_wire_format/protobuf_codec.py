"""Version-skew helpers for generated Protocol Buffers messages."""

from __future__ import annotations

from lab_17_wire_format.pb import task_v1_pb2, task_v2_pb2

MAX_MESSAGE_BYTES = 1024 * 1024


def decode_v1(body: bytes, *, maximum: int = MAX_MESSAGE_BYTES) -> task_v1_pb2.Task:
    """Decode with the old schema after enforcing the framing limit."""
    if len(body) > maximum:
        raise ValueError(f"message exceeds {maximum} bytes")
    message = task_v1_pb2.Task()
    message.ParseFromString(body)
    return message


def decode_v2(body: bytes, *, maximum: int = MAX_MESSAGE_BYTES) -> task_v2_pb2.Task:
    """Decode with the new schema after enforcing the framing limit."""
    if len(body) > maximum:
        raise ValueError(f"message exceeds {maximum} bytes")
    message = task_v2_pb2.Task()
    message.ParseFromString(body)
    return message
