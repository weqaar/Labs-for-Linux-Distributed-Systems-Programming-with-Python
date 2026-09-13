"""Loopback TCP echo components for the relay checkpoint."""

from __future__ import annotations

from .echo import EchoClient, EchoServer
from .models import RelayTask, TaskState
from .packet_journey import (
    LayerSnapshot,
    PacketEndpoint,
    PacketJourney,
    decode_chat_frame,
    trace_chat_message,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "EchoClient",
    "EchoServer",
    "LayerSnapshot",
    "PacketEndpoint",
    "PacketJourney",
    "RelayTask",
    "TaskState",
    "decode_chat_frame",
    "trace_chat_message",
]
