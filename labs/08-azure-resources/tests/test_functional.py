"""Functional tests for checkpoint 08.

These tests drive the public package interface of `lab_08_azure_resources` the
way a deployer would: build a `RelayDeploymentSpec`, plan and apply it against
the in-memory `DeploymentState`, check access with the planned roles, tear it
down, and load the checked-in Bicep tree. No Azure subscription is used.
"""

# pyright: strict

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lab_08_azure_resources import (
    DeploymentState,
    DesiredResource,
    RelayAction,
    RelayDeploymentSpec,
    ResourceKind,
    allows_action,
    deployment_commands,
    load_bicep_checkpoint,
    role_assignments_for_principal,
)

LAB_ROOT = Path(__file__).parents[1]


def staging_spec() -> RelayDeploymentSpec:
    return RelayDeploymentSpec(
        subscription_id="sub-0001",
        location="westeurope",
        resource_group_name="rg-relay-staging",
        storage_account_name="relaytasksstaging",
        relay_identity_name="relay-worker",
        relay_identity_principal_id="principal-relay-worker",
        deployer_principal_id="principal-relay-deployer",
    )


def test_environment_is_deployed_used_and_torn_down_to_an_empty_state() -> None:
    spec = staging_spec()
    empty = DeploymentState()

    deployed = spec.plan_apply(empty).apply(empty)
    worker = role_assignments_for_principal(deployed, spec.relay_identity_principal_id)
    deployer = role_assignments_for_principal(deployed, spec.deployer_principal_id)

    assert spec.plan_apply(deployed).operations == ()
    assert allows_action(worker, RelayAction.READ_TASK_BLOB)
    assert allows_action(worker, RelayAction.ENQUEUE_TASK_MESSAGE)
    assert not allows_action(worker, RelayAction.MANAGE_INFRASTRUCTURE)
    assert allows_action(deployer, RelayAction.MANAGE_INFRASTRUCTURE)
    assert not allows_action(deployer, RelayAction.READ_TASK_BLOB)

    destroyed = spec.plan_destroy(deployed).apply(deployed)

    assert destroyed == DeploymentState()
    assert role_assignments_for_principal(destroyed, spec.relay_identity_principal_id) == ()
    assert spec.plan_destroy(destroyed).operations == ()


def test_drifted_environment_plan_recreates_only_the_missing_queue() -> None:
    spec = staging_spec()
    deployed = spec.plan_apply(DeploymentState()).apply(DeploymentState())
    queue: DesiredResource = next(
        resource for resource in deployed.resources if resource.ref.kind is ResourceKind.QUEUE
    )
    drifted = DeploymentState(
        resources=deployed.resources - {queue},
        role_assignments=deployed.role_assignments,
    )

    repair = spec.plan_apply(drifted)

    assert repair.labels() == ("create:queue:tasks",)
    assert repair.apply(drifted) == deployed


def test_checked_in_bicep_tree_loads_and_staging_commands_preview_first() -> None:
    checkpoint = load_bicep_checkpoint(LAB_ROOT)
    commands = deployment_commands(resource_group="rg-relay-staging", environment="staging")

    assert "staging" in checkpoint.parameters
    assert commands.what_if[:4] == ("az", "deployment", "group", "what-if")
    assert "infra/environments/staging.bicepparam" in commands.what_if
    assert (LAB_ROOT / "infra/environments/staging.bicepparam").is_file()
    assert commands.stack_delete[:4] == ("az", "stack", "group", "delete")


def test_bicep_tree_with_an_embedded_storage_key_is_rejected(tmp_path: Path) -> None:
    shutil.copytree(LAB_ROOT / "infra", tmp_path / "infra")
    storage = tmp_path / "infra/modules/storage.bicep"
    storage.write_text(
        storage.read_text(encoding="utf-8") + "\noutput key string = account.listKeys()\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity instead of embedded credentials"):
        load_bicep_checkpoint(tmp_path)


def test_bicep_tree_missing_an_environment_is_rejected(tmp_path: Path) -> None:
    shutil.copytree(LAB_ROOT / "infra", tmp_path / "infra")
    (tmp_path / "infra/environments/prod.bicepparam").unlink()

    with pytest.raises(ValueError, match="dev, staging, and prod"):
        load_bicep_checkpoint(tmp_path)
