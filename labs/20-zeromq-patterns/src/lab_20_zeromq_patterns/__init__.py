"""ZeroMQ pattern semantics for the relay task service."""

from __future__ import annotations

from lab_20_zeromq_patterns.background_worker import (
    AcceptedBackgroundTask,
    CelerySettings,
    RetryableTaskError,
    create_relay_celery,
    submit_background_task,
    task_delivery_policy,
)
from lab_20_zeromq_patterns.compute_pool import (
    ComputeOutcome,
    ComputePoolClosedError,
    PermanentComputeError,
    RayComputeSettings,
    RelayComputePool,
    RetryableComputeError,
    TaskFingerprintConflictError,
    UnknownResourceLabelError,
    start_local_cluster,
    stop_local_cluster,
)
from lab_20_zeromq_patterns.contract import (
    CommandReply,
    CommandRequest,
    ContractError,
    TaskAction,
    TaskState,
    TaskStatusEvent,
    TaskSubmission,
)
from lab_20_zeromq_patterns.live_pubsub import pubsub_round_trip
from lab_20_zeromq_patterns.patterns import (
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
    "ComputeOutcome",
    "ComputePoolClosedError",
    "PermanentComputeError",
    "RayComputeSettings",
    "RelayComputePool",
    "RetryableComputeError",
    "TaskFingerprintConflictError",
    "UnknownResourceLabelError",
    "start_local_cluster",
    "stop_local_cluster",
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
    "AcceptedBackgroundTask",
    "CelerySettings",
    "RetryableTaskError",
    "__version__",
    "create_relay_celery",
    "pubsub_round_trip",
    "submit_background_task",
    "task_delivery_policy",
]
