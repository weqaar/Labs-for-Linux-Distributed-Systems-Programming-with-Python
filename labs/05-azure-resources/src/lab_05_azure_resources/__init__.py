"""Relay checkpoint 05: an idempotent Azure resource plan."""

from __future__ import annotations

from .deployment import (
    DeploymentPlan,
    DeploymentState,
    DesiredResource,
    PlanAction,
    Plane,
    PlannedOperation,
    RelayAction,
    RelayDeploymentSpec,
    ResourceKind,
    ResourceRef,
    RoleAssignment,
    RoleName,
    allows_action,
    role_assignments_for_principal,
)

__version__ = "0.1.0"

__all__ = [
    "DeploymentPlan",
    "DeploymentState",
    "DesiredResource",
    "Plane",
    "PlanAction",
    "PlannedOperation",
    "RelayAction",
    "RelayDeploymentSpec",
    "ResourceKind",
    "ResourceRef",
    "RoleAssignment",
    "RoleName",
    "__version__",
    "allows_action",
    "role_assignments_for_principal",
]
