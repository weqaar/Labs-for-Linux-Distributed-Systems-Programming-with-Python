"""Length-prefixed framing for the relay checkpoints."""

from __future__ import annotations

from .framing import (
    HEADER_SIZE,
    MAX_FRAME_SIZE,
    FrameError,
    FrameTooLargeError,
    IncrementalFrameReader,
    UnexpectedEOFError,
    decode_frame,
    encode_frame,
    render_task_frame,
    send_frame,
)
from .models import RelayTask, TaskState

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "HEADER_SIZE",
    "MAX_FRAME_SIZE",
    "FrameError",
    "FrameTooLargeError",
    "IncrementalFrameReader",
    "RelayTask",
    "TaskState",
    "UnexpectedEOFError",
    "decode_frame",
    "encode_frame",
    "render_task_frame",
    "send_frame",
]
