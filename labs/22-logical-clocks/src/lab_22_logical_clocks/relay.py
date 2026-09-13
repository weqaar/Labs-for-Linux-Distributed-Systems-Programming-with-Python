"""Relay task updates annotated with Lamport and vector clocks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from lab_22_logical_clocks.clocks import ClockRelation, LamportClock, LamportStamp, VectorClock


class ContractError(ValueError):
    """Raised when a relay task update breaks the shared contract."""


class TaskAction(str, Enum):
    """Supported relay actions."""

    INDEX = "index"
    DELIVER = "deliver"
    ARCHIVE = "archive"


class TaskState(str, Enum):
    """Task states shared by every relay checkpoint."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskUpdate:
    """One task update with both physical and logical time."""

    id: str
    action: TaskAction
    state: TaskState
    actor: str
    detail: str
    wall_time: datetime
    lamport: LamportStamp
    vector: VectorClock

    def __post_init__(self) -> None:
        _require_task_id(self.id)
        if not self.actor.strip():
            raise ContractError("actor must not be empty")
        if not self.detail.strip():
            raise ContractError("detail must not be empty")
        object.__setattr__(self, "wall_time", ensure_utc(self.wall_time))

    def relation_to(self, other: TaskUpdate) -> ClockRelation:
        return self.vector.compare(other.vector)

    def merged_vector(self, other: TaskUpdate) -> VectorClock:
        return self.vector.merge(other.vector)

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "action": self.action.value,
            "state": self.state.value,
            "actor": self.actor,
            "detail": self.detail,
            "wall_time": self.wall_time.isoformat().replace("+00:00", "Z"),
            "lamport": {
                "counter": self.lamport.counter,
                "node_id": self.lamport.node_id,
            },
            "vector": self.vector.to_mapping(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))


@dataclass
class RelayReplica:
    """Replica that tags relay task updates with logical clocks."""

    node_id: str
    _lamport: LamportClock = field(init=False)
    _vectors: dict[str, VectorClock] = field(default_factory=dict)
    _history: dict[str, list[TaskUpdate]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lamport = LamportClock(self.node_id)

    def record_local_update(
        self,
        task_id: str,
        *,
        action: TaskAction,
        state: TaskState,
        detail: str,
        wall_time: datetime,
    ) -> TaskUpdate:
        base_vector = self._vectors.get(task_id, VectorClock())
        next_vector = base_vector.increment(self.node_id)
        update = TaskUpdate(
            id=task_id,
            action=action,
            state=state,
            actor=self.node_id,
            detail=detail,
            wall_time=wall_time,
            lamport=self._lamport.local_event(),
            vector=next_vector,
        )
        self._vectors[task_id] = next_vector
        self._history.setdefault(task_id, []).append(update)
        return update

    def observe_remote_update(self, update: TaskUpdate) -> None:
        merged = self._vectors.get(update.id, VectorClock()).merge(update.vector)
        self._vectors[update.id] = merged
        self._lamport.observe(update.lamport)
        self._history.setdefault(update.id, []).append(update)

    def history(self, task_id: str) -> tuple[TaskUpdate, ...]:
        return tuple(self._history.get(task_id, []))


def logical_order(updates: tuple[TaskUpdate, ...]) -> tuple[TaskUpdate, ...]:
    """Provide a deterministic total order for relay updates."""

    return tuple(sorted(updates, key=lambda update: update.lamport.sort_key()))


def wall_clock_order(updates: tuple[TaskUpdate, ...]) -> tuple[TaskUpdate, ...]:
    """Naive order that can be wrong when node clocks skew."""

    return tuple(sorted(updates, key=lambda update: update.wall_time))


def ensure_utc(value: datetime) -> datetime:
    """Reject naive or non-UTC timestamps and normalise zero offsets."""

    offset = value.utcoffset()
    if offset is None:
        raise ContractError("timestamp must be timezone aware")
    if offset != timedelta(0):
        raise ContractError("timestamp must be in UTC")
    return value.astimezone(timezone.utc)


def _require_task_id(value: str) -> None:
    if not value.startswith("task-") or not value[5:].isdigit() or value[5:].startswith("0"):
        raise ContractError("id must match task-<positive integer>")
