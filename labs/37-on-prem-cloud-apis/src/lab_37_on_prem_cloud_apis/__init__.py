"""Typed cloud adapters and an idempotent resource reconciler."""

from .adapters import HarborAdapter, KubernetesAdapter, OpenStackAdapter
from .core import (
    Action,
    DeadlineExceeded,
    DesiredResource,
    Operation,
    Page,
    PlannedChange,
    RateLimited,
    Reconciler,
    RequestLost,
    ResourceAdapter,
    ResourceConflict,
    ResourceState,
    ResponseLost,
    ServiceUnavailable,
    UnknownOutcome,
)
from .fakes import FakeAdapter
from .redfish import RedfishClient, RedfishProtocolError, RedfishSystem

__all__ = [
    "Action",
    "DeadlineExceeded",
    "DesiredResource",
    "FakeAdapter",
    "HarborAdapter",
    "KubernetesAdapter",
    "OpenStackAdapter",
    "Operation",
    "Page",
    "PlannedChange",
    "RateLimited",
    "RedfishClient",
    "RedfishProtocolError",
    "RedfishSystem",
    "Reconciler",
    "RequestLost",
    "ResourceConflict",
    "ResourceAdapter",
    "ResourceState",
    "ResponseLost",
    "ServiceUnavailable",
    "UnknownOutcome",
]
