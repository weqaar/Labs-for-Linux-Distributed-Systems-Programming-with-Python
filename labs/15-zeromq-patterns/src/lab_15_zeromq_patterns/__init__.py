"""ZeroMQ pattern semantics for the relay task service."""

from __future__ import annotations

from lab_15_zeromq_patterns.contract import (
    CommandReply,
    CommandRequest,
    ContractError,
    TaskAction,
    TaskState,
    TaskStatusEvent,
    TaskSubmission,
)
from lab_15_zeromq_patterns.patterns import (
    DealerClient,
    DealerRouterChannel,
    PipelineStoppedError,
    RelayWorkPipeline,
    ReqStyleClient,
    RequestWedgeError,
    StatusPublisher,
    StatusSubscription,
    SubscriberMetrics,
    UnknownTaskError,
)

__version__ = "0.1.0"

__all__ = [
    "CommandReply",
    "CommandRequest",
    "ContractError",
    "DealerClient",
    "DealerRouterChannel",
    "PipelineStoppedError",
    "RelayWorkPipeline",
    "ReqStyleClient",
    "RequestWedgeError",
    "StatusPublisher",
    "StatusSubscription",
    "SubscriberMetrics",
    "TaskAction",
    "TaskState",
    "TaskStatusEvent",
    "TaskSubmission",
    "UnknownTaskError",
    "__version__",
]
