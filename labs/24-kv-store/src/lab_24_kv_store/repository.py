"""Cosmos-like relay task repository with a fake in-memory client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count

from lab_24_kv_store.contract import RelayTaskRecord, TaskKey, TaskPatch, TaskStatus

UTC = timezone.utc


class TaskAlreadyExistsError(Exception):
    """Raised when creating an existing task."""


class TaskNotFoundError(Exception):
    """Raised when a task does not exist or has expired."""


class ConditionalWriteFailedError(Exception):
    """Raised when an ETag precondition does not match."""


@dataclass(frozen=True)
class TtlPolicy:
    """Container TTL settings."""

    default_ttl_seconds: int | None


@dataclass(frozen=True)
class IndexingPolicy:
    """Subset of the Cosmos indexing policy surface."""

    automatic: bool
    mode: str
    included_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]


@dataclass(frozen=True)
class ContainerModel:
    """Container metadata used by the fake client."""

    container_name: str
    partition_key_path: str
    ttl_policy: TtlPolicy
    indexing_policy: IndexingPolicy


@dataclass(frozen=True)
class StoredTask:
    """Persisted task plus storage metadata."""

    record: RelayTaskRecord
    etag: str
    expires_at: datetime | None


@dataclass(frozen=True)
class RequestCharge:
    """Deterministic request unit accounting."""

    operation: str
    request_units: float
    cross_partition: bool
    partitions_touched: int


@dataclass(frozen=True)
class ItemResult:
    """Result wrapper for point reads and writes."""

    task: StoredTask
    charge: RequestCharge


@dataclass(frozen=True)
class QueryResult:
    """Result wrapper for queries."""

    tasks: tuple[StoredTask, ...]
    charge: RequestCharge


class FixedClock:
    """Mutable clock for deterministic TTL tests."""

    def __init__(self, current_time: datetime) -> None:
        if current_time.tzinfo != UTC:
            raise ValueError("current_time must be timezone.utc aware")
        self._current_time = current_time

    def now(self) -> datetime:
        return self._current_time

    def advance(self, *, seconds: int = 0, minutes: int = 0) -> None:
        self._current_time += timedelta(seconds=seconds, minutes=minutes)


class FakeCosmosClient:
    """Small task-focused fake of the Cosmos client."""

    def __init__(self, *, clock: FixedClock | None = None) -> None:
        self.clock = clock or FixedClock(datetime(2026, 1, 1, tzinfo=UTC))

    def create_task_container(self, model: ContainerModel) -> FakeCosmosContainer:
        return FakeCosmosContainer(model=model, clock=self.clock)


class FakeCosmosContainer:
    """Fake container with point reads, conditional writes, and query accounting."""

    def __init__(self, *, model: ContainerModel, clock: FixedClock) -> None:
        self.model = model
        self._clock = clock
        self._etag_counter = count(1)
        self._items: dict[TaskKey, StoredTask] = {}

    def create_item(
        self,
        record: RelayTaskRecord,
        *,
        ttl_seconds: int | None = None,
    ) -> ItemResult:
        self._purge_expired()
        if record.key in self._items:
            raise TaskAlreadyExistsError(record.key.task_id)
        stored = self._stored_task(record, ttl_seconds=ttl_seconds)
        self._items[record.key] = stored
        return ItemResult(
            task=stored,
            charge=RequestCharge(
                operation="create",
                request_units=5.0,
                cross_partition=False,
                partitions_touched=1,
            ),
        )

    def read_item(self, key: TaskKey) -> ItemResult:
        self._purge_expired()
        stored = self._items.get(key)
        if stored is None:
            raise TaskNotFoundError(key.task_id)
        return ItemResult(
            task=stored,
            charge=RequestCharge(
                operation="point_read",
                request_units=1.0,
                cross_partition=False,
                partitions_touched=1,
            ),
        )

    def replace_item(
        self,
        record: RelayTaskRecord,
        *,
        if_match: str,
        ttl_seconds: int | None = None,
    ) -> ItemResult:
        self._purge_expired()
        current = self._items.get(record.key)
        if current is None:
            raise TaskNotFoundError(record.key.task_id)
        if current.etag != if_match:
            raise ConditionalWriteFailedError(record.key.task_id)
        stored = self._stored_task(record, ttl_seconds=ttl_seconds)
        self._items[record.key] = stored
        return ItemResult(
            task=stored,
            charge=RequestCharge(
                operation="replace",
                request_units=5.0,
                cross_partition=False,
                partitions_touched=1,
            ),
        )

    def query_items(
        self,
        *,
        status: TaskStatus | None = None,
        tenant_id: str | None = None,
    ) -> QueryResult:
        self._purge_expired()
        tasks = tuple(
            sorted(
                (
                    stored
                    for stored in self._items.values()
                    if (tenant_id is None or stored.record.key.tenant_id == tenant_id)
                    and (status is None or stored.record.status is status)
                ),
                key=lambda stored: (stored.record.key.tenant_id, stored.record.key.task_id),
            )
        )
        if tenant_id is None:
            partitions_touched = max(1, len({item.record.key.tenant_id for item in tasks}))
            cross_partition = True
            request_units = 4.0 + partitions_touched
        else:
            partitions_touched = 1
            cross_partition = False
            request_units = 2.0
        return QueryResult(
            tasks=tasks,
            charge=RequestCharge(
                operation="query",
                request_units=request_units,
                cross_partition=cross_partition,
                partitions_touched=partitions_touched,
            ),
        )

    def _stored_task(
        self,
        record: RelayTaskRecord,
        *,
        ttl_seconds: int | None,
    ) -> StoredTask:
        effective_ttl = (
            self.model.ttl_policy.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        )
        expires_at = None
        if effective_ttl is not None:
            expires_at = self._clock.now() + timedelta(seconds=effective_ttl)
        return StoredTask(
            record=record,
            etag=f"{next(self._etag_counter):08d}",
            expires_at=expires_at,
        )

    def _purge_expired(self) -> None:
        current_time = self._clock.now()
        expired = [
            key
            for key, stored in self._items.items()
            if stored.expires_at is not None and stored.expires_at <= current_time
        ]
        for key in expired:
            del self._items[key]


class CosmosTaskRepository:
    """Relay task repository that models the Cosmos access patterns used in the book."""

    rejected_partition_keys = {
        "status": "Rejected because queued work would hot-spot one logical partition.",
        "task_id": "Rejected because tenant-scoped queries would fan out across partitions.",
    }

    def __init__(self, container: FakeCosmosContainer) -> None:
        self._container = container

    @staticmethod
    def default_container_model() -> ContainerModel:
        return ContainerModel(
            container_name="relay-tasks",
            partition_key_path="/tenant_id",
            ttl_policy=TtlPolicy(default_ttl_seconds=86_400),
            indexing_policy=IndexingPolicy(
                automatic=True,
                mode="consistent",
                included_paths=("/tenant_id/?", "/status/?", "/depends_on/*", "/title/?"),
                excluded_paths=("/payload/*",),
            ),
        )

    def partition_key_for(self, key: TaskKey | RelayTaskRecord) -> str:
        if isinstance(key, TaskKey):
            return key.tenant_id
        return key.key.tenant_id

    def create_task(
        self,
        record: RelayTaskRecord,
        *,
        ttl_seconds: int | None = None,
    ) -> ItemResult:
        return self._container.create_item(record, ttl_seconds=ttl_seconds)

    def get_task(self, key: TaskKey) -> ItemResult:
        return self._container.read_item(key)

    def patch_task(self, key: TaskKey, patch: TaskPatch, *, if_match: str) -> ItemResult:
        current = self._container.read_item(key).task
        updated = RelayTaskRecord(
            key=key,
            title=current.record.title if patch.title is None else patch.title,
            status=current.record.status if patch.status is None else patch.status,
            payload=current.record.payload if patch.payload is None else dict(patch.payload),
            depends_on=current.record.depends_on if patch.depends_on is None else patch.depends_on,
        )
        return self._container.replace_item(
            updated,
            if_match=if_match,
            ttl_seconds=_remaining_ttl_seconds(current.expires_at, self._container._clock.now()),
        )

    def query_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        tenant_id: str | None = None,
    ) -> QueryResult:
        return self._container.query_items(status=status, tenant_id=tenant_id)


def _remaining_ttl_seconds(expires_at: datetime | None, current_time: datetime) -> int | None:
    if expires_at is None:
        return None
    remaining = int((expires_at - current_time).total_seconds())
    return max(remaining, 0)
