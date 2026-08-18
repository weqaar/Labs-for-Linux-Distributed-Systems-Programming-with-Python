"""Relay DAG contract for the graph engine checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AttemptDisposition(str, Enum):
    """Supported attempt outcomes."""

    SUCCESS = "success"
    RETRY = "retry"
    FAILURE = "failure"


class NodeState(str, Enum):
    """Execution state for one node."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RelayNodeSpec:
    """One relay DAG node."""

    task_id: str
    title: str
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 1


@dataclass(frozen=True)
class AttemptOutcome:
    """Planned outcome for a node attempt."""

    disposition: AttemptDisposition
    duration: int = 1
    detail: str = ""
