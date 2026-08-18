"""Helpers that summarise relay task frames."""

from __future__ import annotations

from dataclasses import dataclass

from .models import RelayTask
from .native import FramingImplementation, resolve_implementation
from .pure import HEADER_SIZE, MAX_FRAME_SIZE, ReadableBuffer, coerce_bytes, encode_frame


@dataclass(frozen=True)
class FrameDigest:
    """Length and checksum for an encoded relay frame."""

    implementation: str
    payload_length: int
    checksum: int


def render_task_frame(task: RelayTask, *, maximum_frame_size: int = MAX_FRAME_SIZE) -> bytes:
    return encode_frame(task.to_payload(), maximum_frame_size=maximum_frame_size)


def describe_frame(
    frame: ReadableBuffer,
    *,
    maximum_frame_size: int = MAX_FRAME_SIZE,
    implementation: FramingImplementation | None = None,
) -> FrameDigest:
    raw = coerce_bytes(frame)
    impl = resolve_implementation() if implementation is None else implementation
    payload_length = impl.parse_length_prefix(raw, maximum_frame_size=maximum_frame_size)
    payload = raw[HEADER_SIZE:]
    if len(payload) != payload_length:
        raise ValueError("frame payload truncated")
    return FrameDigest(
        implementation=impl.name,
        payload_length=payload_length,
        checksum=impl.fletcher16(payload),
    )
