"""Relay Cosmos-like key-value store checkpoint."""

from __future__ import annotations

from lab_24_kv_store.contract import RelayTaskRecord, TaskKey, TaskPatch, TaskStatus
from lab_24_kv_store.repository import (
    ConditionalWriteFailedError,
    ContainerModel,
    CosmosTaskRepository,
    FakeCosmosClient,
    FakeCosmosContainer,
    FixedClock,
    IndexingPolicy,
    ItemResult,
    QueryResult,
    RequestCharge,
    StoredTask,
    TaskAlreadyExistsError,
    TaskNotFoundError,
    TtlPolicy,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ConditionalWriteFailedError",
    "ContainerModel",
    "CosmosTaskRepository",
    "FakeCosmosClient",
    "FakeCosmosContainer",
    "FixedClock",
    "IndexingPolicy",
    "ItemResult",
    "QueryResult",
    "RelayTaskRecord",
    "RequestCharge",
    "StoredTask",
    "TaskAlreadyExistsError",
    "TaskKey",
    "TaskNotFoundError",
    "TaskPatch",
    "TaskStatus",
    "TtlPolicy",
]
