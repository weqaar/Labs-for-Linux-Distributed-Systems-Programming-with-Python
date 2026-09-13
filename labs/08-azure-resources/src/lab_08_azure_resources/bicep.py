"""Offline contracts for the relay Bicep deployment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FORBIDDEN = re.compile(
    r"listKeys|connectionString|accountKey|clientSecret|adminUserEnabled:\s*true",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BicepCheckpoint:
    """Bicep source and environment parameters checked by the lab."""

    main: str
    platform_module: str
    storage_module: str
    parameters: dict[str, str]


@dataclass(frozen=True, slots=True)
class AzureDeploymentCommands:
    """Reviewable Azure CLI commands for one environment."""

    what_if: tuple[str, ...]
    deploy: tuple[str, ...]
    stack_deploy: tuple[str, ...]
    stack_delete: tuple[str, ...]


def load_bicep_checkpoint(root: Path) -> BicepCheckpoint:
    """Load the checked-in Bicep source tree."""

    infra = root / "infra"
    parameters = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted((infra / "environments").glob("*.bicepparam"))
    }
    checkpoint = BicepCheckpoint(
        main=(infra / "main.bicep").read_text(encoding="utf-8"),
        platform_module=(infra / "modules/platform.bicep").read_text(encoding="utf-8"),
        storage_module=(infra / "modules/storage.bicep").read_text(encoding="utf-8"),
        parameters=parameters,
    )
    validate_bicep_checkpoint(checkpoint)
    return checkpoint


def validate_bicep_checkpoint(checkpoint: BicepCheckpoint) -> None:
    """Require modules, identities, RBAC, AKS, and environment separation."""

    combined = "\n".join((checkpoint.main, checkpoint.platform_module, checkpoint.storage_module))
    required = (
        "targetScope = 'resourceGroup'",
        "module platform 'modules/platform.bicep'",
        "module storage 'modules/storage.bicep'",
        "Microsoft.ContainerRegistry/registries",
        "Microsoft.ContainerService/managedClusters",
        "oidcIssuerProfile",
        "workloadIdentity",
        "federatedIdentityCredentials",
        "Microsoft.OperationalInsights/workspaces",
        "Microsoft.Storage/storageAccounts",
        "Storage Blob Data Contributor",
        "Storage Queue Data Contributor",
        "AcrPull",
        "allowSharedKeyAccess: false",
    )
    missing = tuple(marker for marker in required if marker not in combined)
    if missing:
        raise ValueError(f"Bicep checkpoint is missing required declarations: {missing}")
    if _FORBIDDEN.search(combined):
        raise ValueError("Bicep checkpoint must use identity instead of embedded credentials")
    if set(checkpoint.parameters) != {"dev", "staging", "prod"}:
        raise ValueError("Bicep checkpoint must define dev, staging, and prod parameters")
    for environment, source in checkpoint.parameters.items():
        if "using '../main.bicep'" not in source:
            raise ValueError(f"{environment} parameters must target main.bicep")
        if f"param environment = '{environment}'" not in source:
            raise ValueError(f"{environment} parameters must declare their environment")


def deployment_commands(
    *,
    resource_group: str,
    environment: str,
) -> AzureDeploymentCommands:
    """Build explicit preview, deploy, stack, and teardown commands."""

    if environment not in {"dev", "staging", "prod"}:
        raise ValueError("environment must be dev, staging, or prod")
    if not resource_group:
        raise ValueError("resource_group must not be empty")
    parameters = f"infra/environments/{environment}.bicepparam"
    common = (
        "--resource-group",
        resource_group,
        "--parameters",
        parameters,
    )
    return AzureDeploymentCommands(
        what_if=("az", "deployment", "group", "what-if", *common),
        deploy=("az", "deployment", "group", "create", *common),
        stack_deploy=(
            "az",
            "stack",
            "group",
            "create",
            "--name",
            f"relay-{environment}",
            *common,
            "--deny-settings-mode",
            "none",
            "--action-on-unmanage",
            "deleteAll",
        ),
        stack_delete=(
            "az",
            "stack",
            "group",
            "delete",
            "--name",
            f"relay-{environment}",
            "--resource-group",
            resource_group,
            "--action-on-unmanage",
            "deleteAll",
            "--yes",
        ),
    )
