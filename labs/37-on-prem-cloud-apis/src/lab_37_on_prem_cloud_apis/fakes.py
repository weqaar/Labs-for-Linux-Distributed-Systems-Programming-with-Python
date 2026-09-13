"""Deterministic in-memory cloud adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .core import DesiredResource, JsonValue, Operation, Page, ResourceState


class _FinishedOperation:
    def __init__(self, state: ResourceState) -> None:
        self._state = state

    def wait(self, deadline: float) -> ResourceState:
        if deadline <= 0:
            raise ValueError("deadline must be positive")
        return self._state


class FakeAdapter:
    """A stateful fake with scripted failures and observable calls."""

    def __init__(
        self,
        resources: Sequence[ResourceState] = (),
        failures: Sequence[Exception] = (),
    ) -> None:
        self.resources = {(item.kind, item.name): item for item in resources}
        self.failures = list(failures)
        self.put_calls: list[tuple[DesiredResource, str]] = []
        self.page_calls: list[str | None] = []

    def get(self, kind: str, name: str) -> ResourceState | None:
        return self.resources.get((kind, name))

    def put(self, desired: DesiredResource, idempotency_key: str) -> Operation:
        self.put_calls.append((desired, idempotency_key))
        if self.failures:
            raise self.failures.pop(0)
        state = ResourceState(desired.kind, desired.name, dict(desired.properties))
        self.resources[(desired.kind, desired.name)] = state
        return _FinishedOperation(state)

    def list_page(self, kind: str, cursor: str | None, limit: int) -> Page:
        self.page_calls.append(cursor)
        values = sorted(
            (item for (item_kind, _), item in self.resources.items() if item_kind == kind),
            key=lambda item: item.name,
        )
        start = int(cursor or "0")
        end = min(start + limit, len(values))
        next_cursor = str(end) if end < len(values) else None
        return Page(values[start:end], next_cursor)

    def project(self, desired: DesiredResource, current: ResourceState) -> Mapping[str, JsonValue]:
        return current.properties
