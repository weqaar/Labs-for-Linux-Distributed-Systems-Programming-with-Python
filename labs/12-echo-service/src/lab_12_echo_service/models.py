"""Relay task wire helpers for the echo-service checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskState(str, Enum):
    """Lifecycle states shared by the relay checkpoints."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RelayTask:
    """A textual relay task payload used before framing arrives."""

    task_id: str
    definition: str
    state: TaskState = TaskState.QUEUED

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.definition.strip():
            raise ValueError("definition must not be empty")

    def to_wire(self) -> bytes:
        return f"{self.task_id}|{self.definition}|{self.state.value}".encode()

    @classmethod
    def from_wire(cls, payload: bytes) -> RelayTask:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("wire payload must be valid UTF-8") from exc
        parts = text.split("|")
        if len(parts) != 3:
            raise ValueError("wire payload must contain task_id|definition|state")
        task_id, definition, state_text = parts
        try:
            state = TaskState(state_text)
        except ValueError as exc:
            raise ValueError(f"unknown task state: {state_text}") from exc
        return cls(task_id=task_id, definition=definition, state=state)
