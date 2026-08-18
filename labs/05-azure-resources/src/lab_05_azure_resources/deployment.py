"""Checkpoint 05 for relay: an offline Azure deployment plan for `/tasks`."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResourceKind(str, Enum):
    """Azure resources used by the relay product."""

    RESOURCE_GROUP = "resource-group"
    STORAGE_ACCOUNT = "storage-account"
    MANAGED_IDENTITY = "managed-identity"
    BLOB_CONTAINER = "blob-container"
    QUEUE = "queue"


class RoleName(str, Enum):
    """The role names that matter for this relay checkpoint."""

    CONTRIBUTOR = "Contributor"
    STORAGE_BLOB_DATA_CONTRIBUTOR = "Storage Blob Data Contributor"
    STORAGE_QUEUE_DATA_CONTRIBUTOR = "Storage Queue Data Contributor"


class Plane(str, Enum):
    """Azure control and data planes stay separate."""

    CONTROL = "control"
    DATA = "data"


class PlanAction(str, Enum):
    """The two deployment actions needed for the plan."""

    CREATE = "create"
    DELETE = "delete"


class RelayAction(str, Enum):
    """Actions checked against the relay RBAC model."""

    MANAGE_INFRASTRUCTURE = "manage_infrastructure"
    READ_TASK_BLOB = "read_task_blob"
    ENQUEUE_TASK_MESSAGE = "enqueue_task_message"


@dataclass(frozen=True)
class ResourceRef:
    """A stable reference to one Azure resource."""

    kind: ResourceKind
    name: str


@dataclass(frozen=True)
class DesiredResource:
    """A desired Azure resource in the relay topology."""

    ref: ResourceRef
    location: str | None
    tags: tuple[tuple[str, str], ...] = ()
    depends_on: tuple[ResourceRef, ...] = ()

    @property
    def kind_name(self) -> str:
        return self.ref.kind.value

    @property
    def name(self) -> str:
        return self.ref.name


@dataclass(frozen=True)
class RoleAssignment:
    """A desired role assignment in the relay topology."""

    principal_id: str
    role_name: RoleName
    scope: ResourceRef
    plane: Plane
    depends_on: tuple[ResourceRef, ...] = ()

    @property
    def kind_name(self) -> str:
        return "role-assignment"

    @property
    def name(self) -> str:
        return (
            f"{self.principal_id}:{self.role_name.value}@{self.scope.kind.value}/{self.scope.name}"
        )


@dataclass(frozen=True)
class PlannedOperation:
    """A create or delete step in the relay deployment plan."""

    action: PlanAction
    target: DesiredResource | RoleAssignment

    @property
    def label(self) -> str:
        return f"{self.action.value}:{self.target.kind_name}:{self.target.name}"


@dataclass(frozen=True)
class DeploymentState:
    """The simulated Azure state used by offline tests."""

    resources: frozenset[DesiredResource] = frozenset()
    role_assignments: frozenset[RoleAssignment] = frozenset()


@dataclass(frozen=True)
class DeploymentPlan:
    """A list of offline operations that can update simulated state."""

    operations: tuple[PlannedOperation, ...]

    def labels(self) -> tuple[str, ...]:
        return tuple(operation.label for operation in self.operations)

    def apply(self, state: DeploymentState) -> DeploymentState:
        resources = set(state.resources)
        role_assignments = set(state.role_assignments)
        for operation in self.operations:
            if isinstance(operation.target, DesiredResource):
                if operation.action is PlanAction.CREATE:
                    resources.add(operation.target)
                else:
                    resources.discard(operation.target)
            else:
                if operation.action is PlanAction.CREATE:
                    role_assignments.add(operation.target)
                else:
                    role_assignments.discard(operation.target)
        return DeploymentState(
            resources=frozenset(resources),
            role_assignments=frozenset(role_assignments),
        )


@dataclass(frozen=True)
class RelayDeploymentSpec:
    """Desired Azure state for the relay product."""

    subscription_id: str
    location: str
    resource_group_name: str
    storage_account_name: str
    relay_identity_name: str
    relay_identity_principal_id: str
    deployer_principal_id: str
    blob_container_name: str = "tasks"
    queue_name: str = "tasks"

    def desired_resources(self) -> tuple[DesiredResource, ...]:
        relay_tags = (("service", "relay"), ("api", "/tasks"))
        resource_group = DesiredResource(
            ref=ResourceRef(ResourceKind.RESOURCE_GROUP, self.resource_group_name),
            location=self.location,
            tags=relay_tags,
        )
        storage_account = DesiredResource(
            ref=ResourceRef(ResourceKind.STORAGE_ACCOUNT, self.storage_account_name),
            location=self.location,
            tags=relay_tags,
            depends_on=(resource_group.ref,),
        )
        managed_identity = DesiredResource(
            ref=ResourceRef(ResourceKind.MANAGED_IDENTITY, self.relay_identity_name),
            location=self.location,
            tags=relay_tags,
            depends_on=(resource_group.ref,),
        )
        blob_container = DesiredResource(
            ref=ResourceRef(ResourceKind.BLOB_CONTAINER, self.blob_container_name),
            location=None,
            tags=relay_tags,
            depends_on=(storage_account.ref,),
        )
        queue = DesiredResource(
            ref=ResourceRef(ResourceKind.QUEUE, self.queue_name),
            location=None,
            tags=relay_tags,
            depends_on=(storage_account.ref,),
        )
        return (
            resource_group,
            storage_account,
            managed_identity,
            blob_container,
            queue,
        )

    def desired_role_assignments(self) -> tuple[RoleAssignment, ...]:
        resource_group = ResourceRef(ResourceKind.RESOURCE_GROUP, self.resource_group_name)
        storage_account = ResourceRef(ResourceKind.STORAGE_ACCOUNT, self.storage_account_name)
        identity = ResourceRef(ResourceKind.MANAGED_IDENTITY, self.relay_identity_name)
        return (
            RoleAssignment(
                principal_id=self.deployer_principal_id,
                role_name=RoleName.CONTRIBUTOR,
                scope=resource_group,
                plane=Plane.CONTROL,
                depends_on=(resource_group,),
            ),
            RoleAssignment(
                principal_id=self.relay_identity_principal_id,
                role_name=RoleName.STORAGE_BLOB_DATA_CONTRIBUTOR,
                scope=storage_account,
                plane=Plane.DATA,
                depends_on=(storage_account, identity),
            ),
            RoleAssignment(
                principal_id=self.relay_identity_principal_id,
                role_name=RoleName.STORAGE_QUEUE_DATA_CONTRIBUTOR,
                scope=storage_account,
                plane=Plane.DATA,
                depends_on=(storage_account, identity),
            ),
        )

    def plan_apply(self, current: DeploymentState) -> DeploymentPlan:
        operations: list[PlannedOperation] = []
        for resource in self.desired_resources():
            if resource not in current.resources:
                operations.append(PlannedOperation(PlanAction.CREATE, resource))
        for role_assignment in self.desired_role_assignments():
            if role_assignment not in current.role_assignments:
                operations.append(PlannedOperation(PlanAction.CREATE, role_assignment))
        return DeploymentPlan(tuple(operations))

    def plan_destroy(self, current: DeploymentState) -> DeploymentPlan:
        operations: list[PlannedOperation] = []
        for role_assignment in reversed(self.desired_role_assignments()):
            if role_assignment in current.role_assignments:
                operations.append(PlannedOperation(PlanAction.DELETE, role_assignment))
        for resource in reversed(self.desired_resources()):
            if resource in current.resources:
                operations.append(PlannedOperation(PlanAction.DELETE, resource))
        return DeploymentPlan(tuple(operations))


def role_assignments_for_principal(
    state: DeploymentState,
    principal_id: str,
) -> tuple[RoleAssignment, ...]:
    """Return the offline role assignments for one principal."""

    return tuple(
        assignment
        for assignment in state.role_assignments
        if assignment.principal_id == principal_id
    )


def allows_action(
    assignments: tuple[RoleAssignment, ...],
    action: RelayAction,
) -> bool:
    """Check whether a principal can perform a relay action."""

    roles = {assignment.role_name for assignment in assignments}
    if action is RelayAction.MANAGE_INFRASTRUCTURE:
        return RoleName.CONTRIBUTOR in roles
    if action is RelayAction.READ_TASK_BLOB:
        return RoleName.STORAGE_BLOB_DATA_CONTRIBUTOR in roles
    return RoleName.STORAGE_QUEUE_DATA_CONTRIBUTOR in roles
