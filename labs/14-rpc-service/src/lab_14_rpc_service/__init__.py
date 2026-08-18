"""HTTP RPC client and fake service for the relay task service."""

from __future__ import annotations

from lab_14_rpc_service.contract import (
    TASKS_COLLECTION_PATH,
    ContractError,
    TaskAction,
    TaskState,
    TaskStatus,
    TaskSubmission,
)
from lab_14_rpc_service.rpc import (
    DeadlineBudget,
    DeadlineExceeded,
    FakeClock,
    FakeTransport,
    HttpRequest,
    HttpResponse,
    IdempotencyConflict,
    RelayHttpService,
    RelayRpcClient,
    RelayRpcError,
    ReplyDroppedError,
    RequestDroppedError,
    RetryDecision,
    RetryOutcome,
    RetryOutcomeKind,
    TaskNotFound,
    TransportFailure,
    TransportFault,
    decide_retry,
)

__version__ = "0.1.0"

__all__ = [
    "TASKS_COLLECTION_PATH",
    "ContractError",
    "DeadlineBudget",
    "DeadlineExceeded",
    "FakeClock",
    "FakeTransport",
    "HttpRequest",
    "HttpResponse",
    "IdempotencyConflict",
    "RelayHttpService",
    "RelayRpcClient",
    "RelayRpcError",
    "ReplyDroppedError",
    "RequestDroppedError",
    "RetryDecision",
    "RetryOutcome",
    "RetryOutcomeKind",
    "TaskAction",
    "TaskNotFound",
    "TaskState",
    "TaskStatus",
    "TaskSubmission",
    "TransportFailure",
    "TransportFault",
    "__version__",
    "decide_retry",
]
