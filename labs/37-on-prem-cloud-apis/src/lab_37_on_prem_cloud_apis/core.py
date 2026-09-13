"""Resource contracts and reconciliation policy."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

log = logging.getLogger(__name__)
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class RequestLost(Exception):
    """The connection failed before the request was sent."""


class ResponseLost(Exception):
    """The server may have applied the request before its response was lost."""


class RateLimited(Exception):
    """The service rejected work temporarily."""

    def __init__(self, retry_after: float = 0.0) -> None:
        super().__init__("service rate limit reached")
        self.retry_after = retry_after


class ServiceUnavailable(Exception):
    """The service returned a temporary failure response."""


class ResourceConflict(Exception):
    """An existing resource cannot be converged safely."""


class DeadlineExceeded(Exception):
    """The operation did not finish by its deadline."""


class UnknownOutcome(Exception):
    """A non-idempotent mutation has an unknown result."""


@dataclass(frozen=True)
class DesiredResource:
    """A resource that should exist with the supplied properties."""

    kind: str
    name: str
    properties: Mapping[str, JsonValue]

    @property
    def idempotency_key(self) -> str:
        document = json.dumps(
            [self.kind, self.name, self.properties],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(document.encode()).hexdigest()


@dataclass(frozen=True)
class ResourceState:
    """The observed properties of one resource."""

    kind: str
    name: str
    properties: Mapping[str, JsonValue]


@dataclass(frozen=True)
class Page:
    """One bounded page and the cursor for the following page."""

    items: Sequence[ResourceState]
    next_cursor: str | None = None


class Operation(Protocol):
    """A server-side operation that may still be running."""

    def wait(self, deadline: float) -> ResourceState: ...


class ResourceAdapter(Protocol):
    """The narrow API used by the reconciler."""

    def get(self, kind: str, name: str) -> ResourceState | None: ...

    def put(self, desired: DesiredResource, idempotency_key: str) -> Operation: ...

    def list_page(self, kind: str, cursor: str | None, limit: int) -> Page: ...

    def project(
        self, desired: DesiredResource, current: ResourceState
    ) -> Mapping[str, JsonValue]: ...


class Action(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    NOOP = "noop"


@dataclass(frozen=True)
class PlannedChange:
    """A comparison between desired and observed state."""

    action: Action
    desired: DesiredResource


def _canonical(properties: Mapping[str, JsonValue]) -> str:
    return json.dumps(properties, sort_keys=True, separators=(",", ":"))


class Reconciler:
    """Plan and apply convergent changes through typed adapters."""

    def __init__(
        self,
        adapters: Mapping[str, ResourceAdapter],
        *,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be positive")
        self._adapters = dict(adapters)
        self._attempts = attempts
        self._sleep = sleep
        self._monotonic = monotonic

    def _adapter(self, kind: str) -> ResourceAdapter:
        try:
            return self._adapters[kind]
        except KeyError as error:
            raise ValueError(f"no adapter for resource kind {kind!r}") from error

    def plan(self, desired: Sequence[DesiredResource]) -> list[PlannedChange]:
        """Read current state and return deterministic changes."""
        changes: list[PlannedChange] = []
        for resource in desired:
            current = self._adapter(resource.kind).get(resource.kind, resource.name)
            if current is None:
                action = Action.CREATE
            elif _canonical(self._adapter(resource.kind).project(resource, current)) != _canonical(
                resource.properties
            ):
                action = Action.UPDATE
            else:
                action = Action.NOOP
            changes.append(PlannedChange(action, resource))
        return changes

    def apply(self, changes: Sequence[PlannedChange], timeout: float) -> list[ResourceState]:
        """Apply changes within one end-to-end monotonic deadline."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = self._monotonic() + timeout
        results: list[ResourceState] = []
        for change in changes:
            if self._monotonic() >= deadline:
                raise DeadlineExceeded("reconciliation deadline expired")
            if change.action is Action.NOOP:
                current = self._adapter(change.desired.kind).get(
                    change.desired.kind, change.desired.name
                )
                if current is None:
                    raise RuntimeError("resource disappeared after planning")
                results.append(current)
                continue
            operation = self._retry_put(change.desired, deadline)
            results.append(operation.wait(deadline))
        return results

    def _retry_put(self, desired: DesiredResource, deadline: float) -> Operation:
        adapter = self._adapter(desired.kind)
        for attempt in range(1, self._attempts + 1):
            try:
                return adapter.put(desired, desired.idempotency_key)
            except RateLimited as error:
                delay = max(error.retry_after, float(2 ** (attempt - 1)))
                failure: Exception = error
            except (RequestLost, ResponseLost, ServiceUnavailable) as error:
                delay = float(2 ** (attempt - 1))
                failure = error
            if attempt == self._attempts or self._monotonic() + delay >= deadline:
                raise failure
            log.info(
                "retrying cloud operation",
                extra={"kind": desired.kind, "name": desired.name, "attempt": attempt},
            )
            self._sleep(delay)
        raise AssertionError("retry loop exhausted")

    def list_all(self, kind: str, page_size: int = 100) -> list[ResourceState]:
        """Consume every page without assuming a snapshot."""
        if page_size < 1:
            raise ValueError("page_size must be positive")
        adapter = self._adapter(kind)
        items: list[ResourceState] = []
        cursor: str | None = None
        seen: set[str | None] = set()
        while cursor not in seen:
            seen.add(cursor)
            page = adapter.list_page(kind, cursor, page_size)
            items.extend(page.items)
            if page.next_cursor is None:
                return items
            cursor = page.next_cursor
        raise RuntimeError("service returned a repeated page cursor")


def run_mutation(
    operation: Callable[[], Operation], *, idempotent: bool, attempts: int = 2
) -> Operation:
    """Retry ambiguous outcomes only when repeating the effect is safe."""
    for attempt in range(attempts):
        try:
            return operation()
        except RequestLost:
            if attempt + 1 == attempts:
                raise
        except ResponseLost as error:
            if not idempotent:
                raise UnknownOutcome("mutation may have completed") from error
            if attempt + 1 == attempts:
                raise
    raise AssertionError("mutation retry loop exhausted")
