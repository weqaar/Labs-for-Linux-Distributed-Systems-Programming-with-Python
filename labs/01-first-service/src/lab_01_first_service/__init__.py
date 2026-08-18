"""Relay checkpoint 01: an in-memory first service core."""

from __future__ import annotations

from .relay import (
    InMemoryRelayService,
    InvalidTaskDefinition,
    InvalidTaskTransition,
    RelayDomainError,
    TaskAlreadyExists,
    TaskDefinition,
    TaskNotFound,
    TaskRecord,
    TaskState,
    render_relayctl_status,
)

__version__ = "0.1.0"

__all__ = [
    "InMemoryRelayService",
    "InvalidTaskDefinition",
    "InvalidTaskTransition",
    "RelayDomainError",
    "TaskAlreadyExists",
    "TaskDefinition",
    "TaskNotFound",
    "TaskRecord",
    "TaskState",
    "__version__",
    "render_relayctl_status",
]
