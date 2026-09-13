"""Relay checkpoint 08: an idempotent Azure resource plan."""

from __future__ import annotations

from .bicep import (
    AzureDeploymentCommands,
    BicepCheckpoint,
    deployment_commands,
    load_bicep_checkpoint,
    validate_bicep_checkpoint,
)
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
    "AzureDeploymentCommands",
    "BicepCheckpoint",
    "__version__",
    "allows_action",
    "deployment_commands",
    "load_bicep_checkpoint",
    "role_assignments_for_principal",
    "validate_bicep_checkpoint",
]
