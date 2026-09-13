"""Tests for the relay Azure resource checkpoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_08_azure_resources import (
    DeploymentState,
    RelayAction,
    RelayDeploymentSpec,
    __version__,
    allows_action,
    deployment_commands,
    load_bicep_checkpoint,
    role_assignments_for_principal,
)

ROOT = Path(__file__).parents[1]


def make_spec() -> RelayDeploymentSpec:
    return RelayDeploymentSpec(
        subscription_id="sub-0001",
        location="westeurope",
        resource_group_name="rg-relay-dev",
        storage_account_name="relaytasksdev",
        relay_identity_name="relay-worker",
        relay_identity_principal_id="principal-relay-worker",
        deployer_principal_id="principal-relay-deployer",
    )


def test_apply_plan_is_idempotent_and_tags_relay_resources() -> None:
    spec = make_spec()
    empty_state = DeploymentState()

    first_plan = spec.plan_apply(empty_state)
    filled_state = first_plan.apply(empty_state)
    second_plan = spec.plan_apply(filled_state)

    assert first_plan.labels() == (
        "create:resource-group:rg-relay-dev",
        "create:storage-account:relaytasksdev",
        "create:managed-identity:relay-worker",
        "create:blob-container:tasks",
        "create:queue:tasks",
        "create:role-assignment:principal-relay-deployer:Contributor@resource-group/rg-relay-dev",
        (
            "create:role-assignment:principal-relay-worker:Storage Blob Data "
            "Contributor@storage-account/relaytasksdev"
        ),
        (
            "create:role-assignment:principal-relay-worker:Storage Queue Data "
            "Contributor@storage-account/relaytasksdev"
        ),
    )
    assert second_plan.operations == ()
    assert (("service", "relay"), ("api", "/tasks")) == spec.desired_resources()[0].tags


def test_destroy_plan_reverses_dependencies_and_leaves_nothing() -> None:
    spec = make_spec()
    empty_state = DeploymentState()
    filled_state = spec.plan_apply(empty_state).apply(empty_state)

    destroy_plan = spec.plan_destroy(filled_state)
    final_state = destroy_plan.apply(filled_state)

    assert destroy_plan.labels() == (
        (
            "delete:role-assignment:principal-relay-worker:Storage Queue Data "
            "Contributor@storage-account/relaytasksdev"
        ),
        (
            "delete:role-assignment:principal-relay-worker:Storage Blob Data "
            "Contributor@storage-account/relaytasksdev"
        ),
        "delete:role-assignment:principal-relay-deployer:Contributor@resource-group/rg-relay-dev",
        "delete:queue:tasks",
        "delete:blob-container:tasks",
        "delete:managed-identity:relay-worker",
        "delete:storage-account:relaytasksdev",
        "delete:resource-group:rg-relay-dev",
    )
    assert final_state == DeploymentState()


def test_contributor_role_cannot_read_task_blobs() -> None:
    spec = make_spec()
    state = spec.plan_apply(DeploymentState()).apply(DeploymentState())

    deployer_assignments = role_assignments_for_principal(state, spec.deployer_principal_id)

    assert allows_action(deployer_assignments, RelayAction.MANAGE_INFRASTRUCTURE)
    assert not allows_action(deployer_assignments, RelayAction.READ_TASK_BLOB)
    assert not allows_action(deployer_assignments, RelayAction.ENQUEUE_TASK_MESSAGE)


def test_relay_identity_gets_data_plane_blob_and_queue_access() -> None:
    spec = make_spec()
    state = spec.plan_apply(DeploymentState()).apply(DeploymentState())

    relay_assignments = role_assignments_for_principal(state, spec.relay_identity_principal_id)

    assert not allows_action(relay_assignments, RelayAction.MANAGE_INFRASTRUCTURE)
    assert allows_action(relay_assignments, RelayAction.READ_TASK_BLOB)
    assert allows_action(relay_assignments, RelayAction.ENQUEUE_TASK_MESSAGE)


def test_bicep_modules_deploy_storage_identity_registry_and_aks() -> None:
    checkpoint = load_bicep_checkpoint(ROOT)

    assert set(checkpoint.parameters) == {"dev", "staging", "prod"}
    assert "Microsoft.ContainerService/managedClusters" in checkpoint.platform_module
    assert "federatedIdentityCredentials" in checkpoint.platform_module
    assert "AcrPull" in checkpoint.platform_module
    assert "allowSharedKeyAccess: false" in checkpoint.storage_module
    assert "Storage Queue Data Contributor" in checkpoint.storage_module


def test_bicep_commands_preview_before_deploy_and_support_stack_teardown() -> None:
    commands = deployment_commands(resource_group="rg-relay-staging", environment="staging")

    assert commands.what_if[:5] == ("az", "deployment", "group", "what-if", "--resource-group")
    assert "infra/environments/staging.bicepparam" in commands.deploy
    assert commands.stack_deploy[:5] == ("az", "stack", "group", "create", "--name")
    assert commands.stack_delete[-1] == "--yes"

    with pytest.raises(ValueError, match="environment"):
        deployment_commands(resource_group="rg-relay", environment="test")


def test_version_is_exposed() -> None:
    assert __version__
