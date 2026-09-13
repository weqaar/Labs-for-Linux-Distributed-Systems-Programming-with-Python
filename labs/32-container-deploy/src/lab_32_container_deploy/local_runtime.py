"""Dry-run local runtime plans for the relay service."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from enum import Enum
from pathlib import Path
from typing import Any, Final, Protocol, TypeAlias

import yaml

from lab_32_container_deploy.checkpoint import lab_root, load_checkpoint

QEMU_DOMAIN_NAMESPACE: Final[str] = "http://libvirt.org/schemas/domain/qemu/1.0"
ET.register_namespace("qemu", QEMU_DOMAIN_NAMESPACE)


class RuntimeSelectionError(ValueError):
    """Raised when a requested runtime cannot satisfy the policy."""


class IsolationBoundary(str, Enum):
    """Isolation levels used by the local runtime planner."""

    CONTAINER = "container"
    UTILITY_VM = "utility-vm"
    VIRTUAL_MACHINE = "virtual-machine"


class RuntimeKind(str, Enum):
    """Runtimes supported by the local runtime planner."""

    QEMU_TCG = "qemu-tcg"
    QEMU_KVM = "qemu-kvm"
    LIBVIRT_KVM = "libvirt-kvm"
    DOCKER = "docker"
    LXC_UNPRIVILEGED = "lxc-unprivileged"
    KATA = "kata"


class QemuAccelerator(str, Enum):
    """Acceleration modes for QEMU guests."""

    TCG = "tcg"
    KVM = "kvm"


@dataclass(frozen=True)
class RuntimeRequirements:
    """Security and isolation properties requested by the caller."""

    minimum_isolation: IsolationBoundary = IsolationBoundary.CONTAINER
    require_hardware_acceleration: bool = False
    require_non_root_workload: bool = True
    require_read_only_root: bool = True


@dataclass(frozen=True)
class HostCapabilities:
    """Host features and control surfaces relevant to local runtimes."""

    operating_system: str
    architecture: str
    qemu_system_path: str | None
    docker_path: str | None
    lxc_execute_path: str | None
    kata_runtime_path: str | None
    virsh_path: str | None
    kvm_device: bool
    user_namespaces: bool
    libvirt_session_socket: bool
    selinux_enforcing: bool
    apparmor_enabled: bool
    docker_python_binding: bool
    libvirt_python_binding: bool

    @classmethod
    def detect(cls) -> HostCapabilities:
        """Detect host capabilities without requiring any runtime to be installed."""

        machine = platform.machine().lower() or "x86_64"
        qemu_name = _default_qemu_binary(machine)
        session_root = _runtime_dir()
        return cls(
            operating_system=platform.system().lower(),
            architecture=machine,
            qemu_system_path=shutil.which(qemu_name) or shutil.which("qemu-system-x86_64"),
            docker_path=shutil.which("docker"),
            lxc_execute_path=shutil.which("lxc-execute") or shutil.which("lxc-start"),
            kata_runtime_path=shutil.which("kata-runtime")
            or shutil.which("containerd-shim-kata-v2"),
            virsh_path=shutil.which("virsh"),
            kvm_device=Path("/dev/kvm").exists(),
            user_namespaces=_user_namespaces_enabled(),
            libvirt_session_socket=(session_root / "libvirt/libvirt-sock").exists(),
            selinux_enforcing=_selinux_enforcing(),
            apparmor_enabled=_apparmor_enabled(),
            docker_python_binding=importlib.util.find_spec("docker") is not None,
            libvirt_python_binding=importlib.util.find_spec("libvirt") is not None,
        )

    @classmethod
    def named_profile(cls, name: str) -> HostCapabilities:
        """Return a deterministic host profile for tests or dry runs."""

        profiles = {
            "portable": cls(
                operating_system="linux",
                architecture="x86_64",
                qemu_system_path="/usr/bin/qemu-system-x86_64",
                docker_path=None,
                lxc_execute_path=None,
                kata_runtime_path=None,
                virsh_path=None,
                kvm_device=False,
                user_namespaces=True,
                libvirt_session_socket=False,
                selinux_enforcing=False,
                apparmor_enabled=True,
                docker_python_binding=False,
                libvirt_python_binding=False,
            ),
            "vm-kvm": cls(
                operating_system="linux",
                architecture="x86_64",
                qemu_system_path="/usr/bin/qemu-system-x86_64",
                docker_path=None,
                lxc_execute_path=None,
                kata_runtime_path=None,
                virsh_path="/usr/bin/virsh",
                kvm_device=True,
                user_namespaces=True,
                libvirt_session_socket=True,
                selinux_enforcing=True,
                apparmor_enabled=True,
                docker_python_binding=False,
                libvirt_python_binding=True,
            ),
            "containers": cls(
                operating_system="linux",
                architecture="x86_64",
                qemu_system_path=None,
                docker_path="/usr/bin/docker",
                lxc_execute_path="/usr/bin/lxc-execute",
                kata_runtime_path=None,
                virsh_path=None,
                kvm_device=False,
                user_namespaces=True,
                libvirt_session_socket=False,
                selinux_enforcing=False,
                apparmor_enabled=True,
                docker_python_binding=False,
                libvirt_python_binding=False,
            ),
            "kata": cls(
                operating_system="linux",
                architecture="x86_64",
                qemu_system_path="/usr/bin/qemu-system-x86_64",
                docker_path="/usr/bin/docker",
                lxc_execute_path=None,
                kata_runtime_path="/usr/bin/kata-runtime",
                virsh_path=None,
                kvm_device=True,
                user_namespaces=True,
                libvirt_session_socket=False,
                selinux_enforcing=False,
                apparmor_enabled=True,
                docker_python_binding=False,
                libvirt_python_binding=False,
            ),
            "all": cls(
                operating_system="linux",
                architecture="x86_64",
                qemu_system_path="/usr/bin/qemu-system-x86_64",
                docker_path="/usr/bin/docker",
                lxc_execute_path="/usr/bin/lxc-execute",
                kata_runtime_path="/usr/bin/kata-runtime",
                virsh_path="/usr/bin/virsh",
                kvm_device=True,
                user_namespaces=True,
                libvirt_session_socket=True,
                selinux_enforcing=True,
                apparmor_enabled=True,
                docker_python_binding=False,
                libvirt_python_binding=True,
            ),
        }
        try:
            return profiles[name]
        except KeyError as exc:
            choices = ", ".join(sorted(profiles))
            raise RuntimeSelectionError(
                f"unknown host profile {name!r}; choose one of {choices}"
            ) from exc


@dataclass(frozen=True)
class RelayRuntimeInputs:
    """Inputs shared by every local runtime plan."""

    name: str = "relay"
    container_image: str = field(default_factory=lambda: load_checkpoint().image)
    guest_disk_image: str = field(
        default_factory=lambda: str(lab_root() / "deploy/relay-local-guest.qcow2")
    )
    seed_iso_image: str = field(
        default_factory=lambda: str(lab_root() / "deploy/relay-local-seed.iso")
    )
    lxc_rootfs_path: str = field(
        default_factory=lambda: str(lab_root() / "deploy/relay-local-rootfs")
    )
    lxc_config_path: str = field(
        default_factory=lambda: str(lab_root() / "deploy/relay-local-lxc.conf")
    )
    qmp_socket_path: str = field(default_factory=lambda: str(lab_root() / "deploy/relay.qmp.sock"))
    kata_runtime_class_path: str = field(
        default_factory=lambda: str(lab_root() / "deploy/relay-kata-runtimeclass.yaml")
    )
    host_port: int = 18080
    guest_port: int = 8080
    memory_mib: int = 1024
    vcpus: int = 2
    guest_uid: int = 10001
    guest_gid: int = 10001
    lxc_subuid_base: int = 100000
    lxc_subgid_base: int = 100000
    kata_runtime_class_name: str = "relay-kata"


@dataclass(frozen=True)
class RuntimeDescriptor:
    """Static facts about a runtime family."""

    isolation: IsolationBoundary
    requires_linux: bool
    requires_kvm: bool
    requires_user_namespaces: bool
    supports_non_root_workload: bool
    supports_read_only_root: bool
    summary: str


RUNTIME_DESCRIPTORS: Final[dict[RuntimeKind, RuntimeDescriptor]] = {
    RuntimeKind.QEMU_TCG: RuntimeDescriptor(
        isolation=IsolationBoundary.VIRTUAL_MACHINE,
        requires_linux=False,
        requires_kvm=False,
        requires_user_namespaces=False,
        supports_non_root_workload=True,
        supports_read_only_root=True,
        summary=(
            "QEMU with TCG keeps a full virtual-machine boundary without KVM. "
            "It is the portable fallback and it is slow."
        ),
    ),
    RuntimeKind.QEMU_KVM: RuntimeDescriptor(
        isolation=IsolationBoundary.VIRTUAL_MACHINE,
        requires_linux=True,
        requires_kvm=True,
        requires_user_namespaces=False,
        supports_non_root_workload=True,
        supports_read_only_root=True,
        summary=(
            "QEMU uses the Linux KVM accelerator for near-native performance. "
            "KVM is an accelerator, not a VM manager."
        ),
    ),
    RuntimeKind.LIBVIRT_KVM: RuntimeDescriptor(
        isolation=IsolationBoundary.VIRTUAL_MACHINE,
        requires_linux=True,
        requires_kvm=True,
        requires_user_namespaces=False,
        supports_non_root_workload=True,
        supports_read_only_root=True,
        summary=(
            "libvirt manages QEMU with the Linux KVM accelerator through a "
            "session-scoped control plane. KVM is not a manager on its own."
        ),
    ),
    RuntimeKind.DOCKER: RuntimeDescriptor(
        isolation=IsolationBoundary.CONTAINER,
        requires_linux=False,
        requires_kvm=False,
        requires_user_namespaces=False,
        supports_non_root_workload=True,
        supports_read_only_root=True,
        summary=(
            "Docker shares the host kernel, so it is fast and operationally simple, "
            "but it keeps a container boundary rather than a VM boundary."
        ),
    ),
    RuntimeKind.LXC_UNPRIVILEGED: RuntimeDescriptor(
        isolation=IsolationBoundary.CONTAINER,
        requires_linux=True,
        requires_kvm=False,
        requires_user_namespaces=True,
        supports_non_root_workload=True,
        supports_read_only_root=True,
        summary=(
            "Unprivileged LXC keeps the workload inside user-namespace remapped "
            "containers. It needs subordinate uid and gid ranges on Linux."
        ),
    ),
    RuntimeKind.KATA: RuntimeDescriptor(
        isolation=IsolationBoundary.UTILITY_VM,
        requires_linux=True,
        requires_kvm=True,
        requires_user_namespaces=False,
        supports_non_root_workload=True,
        supports_read_only_root=True,
        summary=(
            "Kata runs the container inside a utility VM. It keeps container UX "
            "with a VM boundary, and it still depends on KVM on Linux."
        ),
    ),
}


@dataclass(frozen=True)
class QmpCommand:
    """A deterministic QMP command issued after the socket is available."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QemuPlan:
    """A dry-run QEMU launch plan."""

    executable: str
    accelerator: QemuAccelerator
    argv: tuple[str, ...]
    qmp_socket_path: str
    handshake: tuple[QmpCommand, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class LibvirtPlan:
    """A dry-run libvirt domain definition."""

    connection_uri: str
    domain_xml: str
    qmp_socket_path: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class DockerContainerConfig:
    """Structured Docker configuration for the relay container."""

    image: str
    name: str
    command: tuple[str, ...]
    user: str
    read_only: bool
    cap_drop: tuple[str, ...]
    security_opt: tuple[str, ...]
    tmpfs: dict[str, str]
    environment: dict[str, str]
    ports: dict[str, int]
    pids_limit: int


@dataclass(frozen=True)
class DockerPlan:
    """A dry-run Docker launch plan."""

    argv: tuple[str, ...]
    container: DockerContainerConfig
    notes: tuple[str, ...]


@dataclass(frozen=True)
class LxcPlan:
    """A dry-run unprivileged LXC plan."""

    executable: str
    config_path: str
    argv: tuple[str, ...]
    config_lines: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class KataPlan:
    """A dry-run Kata utility-VM plan."""

    runtime_handler: str
    runtime_class_path: str
    runtime_class: dict[str, Any]
    runtime_class_yaml: str
    pod_patch: dict[str, Any]
    notes: tuple[str, ...]


LocalRuntimeDetails: TypeAlias = QemuPlan | LibvirtPlan | DockerPlan | LxcPlan | KataPlan


@dataclass(frozen=True)
class LocalRuntimePlan:
    """The full runtime plan plus host and policy context."""

    runtime: RuntimeKind
    isolation: IsolationBoundary
    summary: str
    selection_reason: str
    launch_ready: bool
    launch_issues: tuple[str, ...]
    requirements: RuntimeRequirements
    host: HostCapabilities
    inputs: RelayRuntimeInputs
    details: LocalRuntimeDetails


@dataclass(frozen=True)
class ExecutionReceipt:
    """A deterministic record of a fake runtime action."""

    runtime: RuntimeKind
    identifier: str
    steps: tuple[str, ...]


class DockerApi(Protocol):
    """Boundary for Docker SDK or HTTP client integrations."""

    def create_container(
        self,
        *,
        name: str,
        image: str,
        command: Sequence[str],
        user: str,
        read_only: bool,
        cap_drop: Sequence[str],
        security_opt: Sequence[str],
        tmpfs: Mapping[str, str],
        environment: Mapping[str, str],
        ports: Mapping[str, int],
        pids_limit: int,
    ) -> str: ...

    def start_container(self, container_id: str) -> None: ...


class LibvirtApi(Protocol):
    """Boundary for libvirt session integrations."""

    def define_domain(self, connection_uri: str, domain_xml: str) -> str: ...

    def start_domain(self, domain_name: str) -> None: ...


class QmpApi(Protocol):
    """Boundary for QMP integrations."""

    def connect(self, socket_path: str) -> None: ...

    def command(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


def plan_runtime(
    requested_runtime: RuntimeKind | None = None,
    *,
    requirements: RuntimeRequirements | None = None,
    host: HostCapabilities | None = None,
    inputs: RelayRuntimeInputs | None = None,
) -> LocalRuntimePlan:
    """Build a dry-run local runtime plan for relay."""

    effective_requirements = requirements or RuntimeRequirements()
    effective_host = host or HostCapabilities.detect()
    effective_inputs = inputs or RelayRuntimeInputs()

    if requested_runtime is None:
        runtime, selection_reason = _select_runtime(effective_requirements, effective_host)
    else:
        _validate_runtime(requested_runtime, effective_requirements, effective_host)
        runtime = requested_runtime
        selection_reason = f"Selected {runtime.value} because it was requested explicitly."

    details = _build_plan_details(runtime, effective_host, effective_inputs)
    launch_issues = tuple(_launch_issues(runtime, effective_host))
    descriptor = RUNTIME_DESCRIPTORS[runtime]
    return LocalRuntimePlan(
        runtime=runtime,
        isolation=descriptor.isolation,
        summary=descriptor.summary,
        selection_reason=selection_reason,
        launch_ready=not launch_issues,
        launch_issues=launch_issues,
        requirements=effective_requirements,
        host=effective_host,
        inputs=effective_inputs,
        details=details,
    )


def start_docker_container(plan: DockerPlan, api: DockerApi) -> ExecutionReceipt:
    """Call an injected Docker client with a validated plan."""

    container_id = api.create_container(
        name=plan.container.name,
        image=plan.container.image,
        command=plan.container.command,
        user=plan.container.user,
        read_only=plan.container.read_only,
        cap_drop=plan.container.cap_drop,
        security_opt=plan.container.security_opt,
        tmpfs=plan.container.tmpfs,
        environment=plan.container.environment,
        ports=plan.container.ports,
        pids_limit=plan.container.pids_limit,
    )
    api.start_container(container_id)
    return ExecutionReceipt(
        runtime=RuntimeKind.DOCKER,
        identifier=container_id,
        steps=("create_container", "start_container"),
    )


def define_libvirt_domain(plan: LibvirtPlan, api: LibvirtApi) -> ExecutionReceipt:
    """Call an injected libvirt client with generated domain XML."""

    domain_name = api.define_domain(plan.connection_uri, plan.domain_xml)
    api.start_domain(domain_name)
    return ExecutionReceipt(
        runtime=RuntimeKind.LIBVIRT_KVM,
        identifier=domain_name,
        steps=("define_domain", "start_domain"),
    )


def negotiate_qmp(plan: QemuPlan, api: QmpApi) -> tuple[Mapping[str, Any], ...]:
    """Call an injected QMP client using the planned Unix socket."""

    responses: list[Mapping[str, Any]] = []
    api.connect(plan.qmp_socket_path)
    try:
        for command in plan.handshake:
            response = api.command(command.name, command.arguments or None)
            responses.append(response)
    finally:
        api.close()
    return tuple(responses)


def render_runtime_plan(plan: LocalRuntimePlan, output_format: str = "json") -> str:
    """Render a runtime plan as JSON or YAML."""

    primitive = _to_primitive(plan)
    if output_format == "json":
        return json.dumps(primitive, indent=2, sort_keys=True)
    if output_format == "yaml":
        return yaml.safe_dump(primitive, sort_keys=False)
    raise RuntimeSelectionError(f"unsupported output format {output_format!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the local runtime planner CLI."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "plan":
        parser.print_help(sys.stderr)
        return 1

    host = (
        HostCapabilities.detect()
        if args.host_profile == "detected"
        else HostCapabilities.named_profile(args.host_profile)
    )

    requirements = RuntimeRequirements(
        minimum_isolation=IsolationBoundary(args.minimum_isolation),
        require_hardware_acceleration=args.require_hardware_acceleration,
    )
    inputs = RelayRuntimeInputs(
        container_image=args.container_image,
        guest_disk_image=args.guest_disk_image,
        seed_iso_image=args.seed_iso_image,
        lxc_rootfs_path=args.lxc_rootfs_path,
        lxc_config_path=args.lxc_config_path,
        qmp_socket_path=args.qmp_socket_path,
        kata_runtime_class_path=args.kata_runtime_class_path,
        memory_mib=args.memory_mib,
        vcpus=args.vcpus,
        host_port=args.host_port,
        guest_port=args.guest_port,
    )
    requested_runtime = None if args.runtime == "auto" else RuntimeKind(args.runtime)

    try:
        plan = plan_runtime(
            requested_runtime,
            requirements=requirements,
            host=host,
            inputs=inputs,
        )
    except RuntimeSelectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(render_runtime_plan(plan, args.format))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan local relay runtime configurations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="print a dry-run runtime plan")
    plan_parser.add_argument(
        "--runtime",
        default="auto",
        choices=["auto", *(runtime.value for runtime in RuntimeKind)],
        help="requested runtime family, or auto to pick one from policy",
    )
    plan_parser.add_argument(
        "--minimum-isolation",
        default=IsolationBoundary.CONTAINER.value,
        choices=[boundary.value for boundary in IsolationBoundary],
    )
    plan_parser.add_argument(
        "--require-hardware-acceleration",
        action="store_true",
        help="fail unless the selected plan uses the Linux KVM accelerator",
    )
    plan_parser.add_argument(
        "--host-profile",
        default="detected",
        choices=["detected", "all", "containers", "kata", "portable", "vm-kvm"],
        help="use detected host data or a deterministic profile",
    )
    default_inputs = RelayRuntimeInputs()
    plan_parser.add_argument("--container-image", default=default_inputs.container_image)
    plan_parser.add_argument("--guest-disk-image", default=default_inputs.guest_disk_image)
    plan_parser.add_argument("--seed-iso-image", default=default_inputs.seed_iso_image)
    plan_parser.add_argument("--lxc-rootfs-path", default=default_inputs.lxc_rootfs_path)
    plan_parser.add_argument("--lxc-config-path", default=default_inputs.lxc_config_path)
    plan_parser.add_argument("--qmp-socket-path", default=default_inputs.qmp_socket_path)
    plan_parser.add_argument(
        "--kata-runtime-class-path", default=default_inputs.kata_runtime_class_path
    )
    plan_parser.add_argument("--memory-mib", default=default_inputs.memory_mib, type=int)
    plan_parser.add_argument("--vcpus", default=default_inputs.vcpus, type=int)
    plan_parser.add_argument("--host-port", default=default_inputs.host_port, type=int)
    plan_parser.add_argument("--guest-port", default=default_inputs.guest_port, type=int)
    plan_parser.add_argument("--format", default="json", choices=["json", "yaml"])
    return parser


def _select_runtime(
    requirements: RuntimeRequirements, host: HostCapabilities
) -> tuple[RuntimeKind, str]:
    candidates = _candidate_order(requirements.minimum_isolation)
    last_error: RuntimeSelectionError | None = None
    for runtime in candidates:
        try:
            _validate_runtime(runtime, requirements, host)
        except RuntimeSelectionError as exc:
            last_error = exc
            continue
        return runtime, _selection_reason(runtime, requirements, host)

    if last_error is not None:
        raise last_error
    raise RuntimeSelectionError("no runtime satisfied the requested policy")


def _candidate_order(minimum_isolation: IsolationBoundary) -> tuple[RuntimeKind, ...]:
    if minimum_isolation is IsolationBoundary.VIRTUAL_MACHINE:
        return (
            RuntimeKind.LIBVIRT_KVM,
            RuntimeKind.QEMU_KVM,
            RuntimeKind.QEMU_TCG,
        )
    if minimum_isolation is IsolationBoundary.UTILITY_VM:
        return (
            RuntimeKind.KATA,
            RuntimeKind.LIBVIRT_KVM,
            RuntimeKind.QEMU_KVM,
            RuntimeKind.QEMU_TCG,
        )
    return (
        RuntimeKind.DOCKER,
        RuntimeKind.LXC_UNPRIVILEGED,
        RuntimeKind.KATA,
        RuntimeKind.LIBVIRT_KVM,
        RuntimeKind.QEMU_KVM,
        RuntimeKind.QEMU_TCG,
    )


def _selection_reason(
    runtime: RuntimeKind, requirements: RuntimeRequirements, host: HostCapabilities
) -> str:
    if runtime is RuntimeKind.QEMU_TCG and not host.kvm_device:
        return (
            "Selected qemu-tcg because the requested boundary needs a VM and /dev/kvm was "
            "not available. TCG keeps the VM boundary with slower software emulation."
        )
    if runtime is RuntimeKind.LIBVIRT_KVM:
        return "Selected libvirt-kvm for a KVM-accelerated VM plan with session-scoped control."
    if runtime is RuntimeKind.QEMU_KVM:
        return "Selected qemu-kvm for a direct KVM-accelerated VM plan."
    if runtime is RuntimeKind.DOCKER:
        return "Selected docker for a hardened container plan on the same relay image."
    if runtime is RuntimeKind.LXC_UNPRIVILEGED:
        return "Selected lxc-unprivileged for a user-namespace remapped container plan."
    if (
        runtime is RuntimeKind.KATA
        and requirements.minimum_isolation is IsolationBoundary.UTILITY_VM
    ):
        return "Selected kata to keep relay inside a utility VM boundary with container UX."
    return f"Selected {runtime.value} because it satisfied the requested policy first."


def _validate_runtime(
    runtime: RuntimeKind, requirements: RuntimeRequirements, host: HostCapabilities
) -> None:
    descriptor = RUNTIME_DESCRIPTORS[runtime]
    if descriptor.requires_linux and host.operating_system != "linux":
        raise RuntimeSelectionError(f"{runtime.value} needs a Linux host")
    if _isolation_rank(descriptor.isolation) < _isolation_rank(requirements.minimum_isolation):
        raise RuntimeSelectionError(
            f"{runtime.value} provides {descriptor.isolation.value} isolation, which is below "
            f"the requested {requirements.minimum_isolation.value} boundary"
        )
    if requirements.require_hardware_acceleration and not descriptor.requires_kvm:
        raise RuntimeSelectionError(
            f"{runtime.value} cannot satisfy the requested KVM-backed acceleration"
        )
    if requirements.require_non_root_workload and not descriptor.supports_non_root_workload:
        raise RuntimeSelectionError(f"{runtime.value} cannot keep the workload non-root")
    if requirements.require_read_only_root and not descriptor.supports_read_only_root:
        raise RuntimeSelectionError(f"{runtime.value} cannot keep the root filesystem read-only")
    if descriptor.requires_kvm and not host.kvm_device:
        raise RuntimeSelectionError(
            f"{runtime.value} needs /dev/kvm. KVM is a Linux accelerator used by QEMU, "
            "libvirt, and Kata, not a standalone VM manager. Use qemu-tcg for a slower "
            "portable fallback or lower the acceleration requirement."
        )
    if descriptor.requires_user_namespaces and not host.user_namespaces:
        raise RuntimeSelectionError(
            f"{runtime.value} needs user namespaces and subordinate uid and gid ranges"
        )


def _build_plan_details(
    runtime: RuntimeKind, host: HostCapabilities, inputs: RelayRuntimeInputs
) -> LocalRuntimeDetails:
    if runtime is RuntimeKind.QEMU_TCG:
        return _build_qemu_plan(host, inputs, QemuAccelerator.TCG)
    if runtime is RuntimeKind.QEMU_KVM:
        return _build_qemu_plan(host, inputs, QemuAccelerator.KVM)
    if runtime is RuntimeKind.LIBVIRT_KVM:
        return _build_libvirt_plan(host, inputs)
    if runtime is RuntimeKind.DOCKER:
        return _build_docker_plan(host, inputs)
    if runtime is RuntimeKind.LXC_UNPRIVILEGED:
        return _build_lxc_plan(host, inputs)
    return _build_kata_plan(inputs)


def _build_qemu_plan(
    host: HostCapabilities, inputs: RelayRuntimeInputs, accelerator: QemuAccelerator
) -> QemuPlan:
    executable = host.qemu_system_path or _default_qemu_binary(host.architecture)
    accel_value = "kvm" if accelerator is QemuAccelerator.KVM else "tcg,thread=multi"
    netdev_value = f"user,id=net0,hostfwd=tcp:127.0.0.1:{inputs.host_port}-:{inputs.guest_port}"
    argv = (
        executable,
        "-name",
        inputs.name,
        "-machine",
        "q35",
        "-accel",
        accel_value,
        "-cpu",
        "max",
        "-smp",
        str(inputs.vcpus),
        "-m",
        str(inputs.memory_mib),
        "-nodefaults",
        "-display",
        "none",
        "-serial",
        "none",
        "-monitor",
        "none",
        "-qmp",
        f"unix:{inputs.qmp_socket_path},server=on,wait=off",
        "-sandbox",
        "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
        "-netdev",
        netdev_value,
        "-device",
        "virtio-net-pci,netdev=net0",
        "-drive",
        f"file={inputs.guest_disk_image},if=virtio,format=qcow2,readonly=on",
        "-drive",
        f"file={inputs.seed_iso_image},if=virtio,media=cdrom,readonly=on",
    )
    notes = [
        "The QMP control plane stays on a Unix socket so it does not expose a network port.",
        "The relay guest disk stays read-only in the plan, so updates belong in a fresh image.",
    ]
    if accelerator is QemuAccelerator.TCG:
        notes.append("TCG is the portable no-KVM fallback, but it is slow.")
    else:
        notes.append("KVM accelerates QEMU, but it does not replace QEMU itself.")
    return QemuPlan(
        executable=executable,
        accelerator=accelerator,
        argv=argv,
        qmp_socket_path=inputs.qmp_socket_path,
        handshake=(QmpCommand("qmp_capabilities"), QmpCommand("query-status")),
        notes=tuple(notes),
    )


def _build_libvirt_plan(host: HostCapabilities, inputs: RelayRuntimeInputs) -> LibvirtPlan:
    domain = ET.Element("domain", {"type": "kvm"})
    ET.SubElement(domain, "name").text = inputs.name
    ET.SubElement(domain, "memory", {"unit": "MiB"}).text = str(inputs.memory_mib)
    ET.SubElement(domain, "currentMemory", {"unit": "MiB"}).text = str(inputs.memory_mib)
    ET.SubElement(domain, "vcpu", {"placement": "static"}).text = str(inputs.vcpus)

    os_element = ET.SubElement(domain, "os")
    ET.SubElement(
        os_element,
        "type",
        {"arch": host.architecture, "machine": "q35"},
    ).text = "hvm"
    ET.SubElement(os_element, "boot", {"dev": "hd"})

    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")
    ET.SubElement(domain, "cpu", {"mode": "host-model"})

    resource = ET.SubElement(domain, "resource")
    ET.SubElement(resource, "partition").text = "/relay"
    ET.SubElement(domain, "seclabel", {"type": "dynamic", "model": "selinux", "relabel": "yes"})

    devices = ET.SubElement(domain, "devices")
    ET.SubElement(devices, "emulator").text = host.qemu_system_path or _default_qemu_binary(
        host.architecture
    )

    root_disk = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
    ET.SubElement(root_disk, "driver", {"name": "qemu", "type": "qcow2", "cache": "none"})
    ET.SubElement(root_disk, "source", {"file": inputs.guest_disk_image})
    ET.SubElement(root_disk, "target", {"dev": "vda", "bus": "virtio"})
    ET.SubElement(root_disk, "readonly")

    seed_disk = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
    ET.SubElement(seed_disk, "driver", {"name": "qemu", "type": "raw"})
    ET.SubElement(seed_disk, "source", {"file": inputs.seed_iso_image})
    ET.SubElement(seed_disk, "target", {"dev": "sda", "bus": "sata"})
    ET.SubElement(seed_disk, "readonly")

    ET.SubElement(devices, "graphics", {"type": "none"})
    ET.SubElement(devices, "console", {"type": "pty"})
    ET.SubElement(devices, "memballoon", {"model": "virtio"})

    netdev_value = f"user,id=net0,hostfwd=tcp:127.0.0.1:{inputs.host_port}-:{inputs.guest_port}"
    commandline = ET.SubElement(domain, f"{{{QEMU_DOMAIN_NAMESPACE}}}commandline")
    for argument in (
        "-qmp",
        f"unix:{inputs.qmp_socket_path},server=on,wait=off",
        "-sandbox",
        "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
        "-netdev",
        netdev_value,
        "-device",
        "virtio-net-pci,netdev=net0",
    ):
        ET.SubElement(commandline, f"{{{QEMU_DOMAIN_NAMESPACE}}}arg", {"value": argument})

    notes = [
        "Use qemu:///session so relay stays under the calling user instead of system libvirtd.",
        "The XML asks for dynamic sVirt labels through a non-root session model.",
    ]
    if not host.selinux_enforcing:
        notes.append(
            "SELinux is not enforcing on this host profile, so keep an equivalent "
            "AppArmor or seccomp policy."
        )
    return LibvirtPlan(
        connection_uri="qemu:///session",
        domain_xml=ET.tostring(domain, encoding="unicode"),
        qmp_socket_path=inputs.qmp_socket_path,
        notes=tuple(notes),
    )


def _build_docker_plan(host: HostCapabilities, inputs: RelayRuntimeInputs) -> DockerPlan:
    executable = host.docker_path or "docker"
    relay_command = _relay_command(inputs)
    container = DockerContainerConfig(
        image=inputs.container_image,
        name=inputs.name,
        command=relay_command,
        user=f"{inputs.guest_uid}:{inputs.guest_gid}",
        read_only=True,
        cap_drop=("ALL",),
        security_opt=("no-new-privileges",),
        tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=65536k"},
        environment={
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        },
        ports={f"{inputs.guest_port}/tcp": inputs.host_port},
        pids_limit=256,
    )
    argv = (
        executable,
        "run",
        "--rm",
        "--name",
        container.name,
        "--user",
        container.user,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(container.pids_limit),
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=65536k",
        "-p",
        f"127.0.0.1:{inputs.host_port}:{inputs.guest_port}",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTHONUNBUFFERED=1",
        container.image,
        *relay_command,
    )
    return DockerPlan(
        argv=argv,
        container=container,
        notes=(
            "Docker keeps the workload non-root, read-only, and without new privileges.",
            "The container still shares the host kernel, so it is not a VM boundary.",
        ),
    )


def _build_lxc_plan(host: HostCapabilities, inputs: RelayRuntimeInputs) -> LxcPlan:
    executable = host.lxc_execute_path or "lxc-execute"
    config_lines = (
        "lxc.include = /usr/share/lxc/config/common.conf",
        f"lxc.uts.name = {inputs.name}",
        f"lxc.rootfs.path = dir:{inputs.lxc_rootfs_path}",
        "lxc.rootfs.options = ro",
        "lxc.apparmor.profile = generated",
        "lxc.apparmor.allow_nesting = 0",
        "lxc.no_new_privs = 1",
        "lxc.mount.auto = proc:mixed sys:ro cgroup:mixed",
        f"lxc.idmap = u 0 {inputs.lxc_subuid_base} 65536",
        f"lxc.idmap = g 0 {inputs.lxc_subgid_base} 65536",
        (
            "lxc.cap.drop = sys_admin sys_module sys_time sys_boot "
            "mac_admin mac_override setfcap sys_rawio"
        ),
        "lxc.net.0.type = veth",
        "lxc.net.0.link = lxcbr0",
        "lxc.net.0.flags = up",
        "lxc.net.0.name = eth0",
        f"lxc.environment = RELAY_PORT={inputs.guest_port}",
    )
    argv = (
        executable,
        "-n",
        inputs.name,
        "-f",
        inputs.lxc_config_path,
        "--clear-env",
        "--",
        "/opt/relay-venv/bin/python",
        "-m",
        "lab_32_container_deploy.relay_api",
        "--host",
        "0.0.0.0",
        "--port",
        str(inputs.guest_port),
    )
    return LxcPlan(
        executable=executable,
        config_path=inputs.lxc_config_path,
        argv=argv,
        config_lines=config_lines,
        notes=(
            "Unprivileged LXC depends on user namespaces plus subordinate uid and gid ranges.",
            "The uid map keeps guest uid 0 away from host uid 0.",
        ),
    )


def _build_kata_plan(inputs: RelayRuntimeInputs) -> KataPlan:
    runtime_class = {
        "apiVersion": "node.k8s.io/v1",
        "kind": "RuntimeClass",
        "metadata": {"name": inputs.kata_runtime_class_name},
        "handler": "kata",
        "overhead": {"podFixed": {"cpu": "250m", "memory": "384Mi"}},
        "scheduling": {"nodeSelector": {"katacontainers.io/kata-runtime": "true"}},
    }
    pod_patch = {
        "metadata": {
            "annotations": {
                "io.katacontainers.config.hypervisor.default_memory": str(inputs.memory_mib),
                "io.katacontainers.config.hypervisor.default_vcpus": str(inputs.vcpus),
            }
        },
        "spec": {
            "runtimeClassName": inputs.kata_runtime_class_name,
            "containers": [
                {
                    "name": inputs.name,
                    "image": inputs.container_image,
                    "command": list(_relay_command(inputs)),
                    "ports": [{"containerPort": inputs.guest_port}],
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": inputs.guest_uid,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                }
            ],
        },
    }
    return KataPlan(
        runtime_handler="kata",
        runtime_class_path=inputs.kata_runtime_class_path,
        runtime_class=runtime_class,
        runtime_class_yaml=yaml.safe_dump(runtime_class, sort_keys=False),
        pod_patch=pod_patch,
        notes=(
            "Kata keeps relay inside a utility VM, sometimes called a UVM, while "
            "preserving container workflows.",
            "The RuntimeClass plan stays offline and does not claim that the host "
            "already has Kata wired into CRI.",
        ),
    )


def _launch_issues(runtime: RuntimeKind, host: HostCapabilities) -> list[str]:
    issues: list[str] = []
    if runtime in {RuntimeKind.QEMU_TCG, RuntimeKind.QEMU_KVM, RuntimeKind.LIBVIRT_KVM}:
        if host.qemu_system_path is None:
            issues.append("qemu-system was not detected on the host")
    if runtime is RuntimeKind.DOCKER:
        if host.docker_path is None and not host.docker_python_binding:
            issues.append("docker CLI or Python binding was not detected on the host")
    if runtime is RuntimeKind.LXC_UNPRIVILEGED and host.lxc_execute_path is None:
        issues.append("lxc-execute or lxc-start was not detected on the host")
    if runtime is RuntimeKind.KATA:
        if host.kata_runtime_path is None:
            issues.append("kata-runtime or containerd-shim-kata-v2 was not detected on the host")
        if host.docker_path is None:
            issues.append("a container CLI was not detected for Kata image workflows")
    if runtime is RuntimeKind.LIBVIRT_KVM:
        if not (host.libvirt_session_socket or host.virsh_path or host.libvirt_python_binding):
            issues.append("libvirt session access was not detected on the host")
    return issues


def _relay_command(inputs: RelayRuntimeInputs) -> tuple[str, ...]:
    return (
        "python",
        "-m",
        "lab_32_container_deploy.relay_api",
        "--host",
        "0.0.0.0",
        "--port",
        str(inputs.guest_port),
    )


def _isolation_rank(boundary: IsolationBoundary) -> int:
    order = {
        IsolationBoundary.CONTAINER: 1,
        IsolationBoundary.UTILITY_VM: 2,
        IsolationBoundary.VIRTUAL_MACHINE: 3,
    }
    return order[boundary]


def _default_qemu_binary(architecture: str) -> str:
    if "aarch64" in architecture or "arm64" in architecture:
        return "qemu-system-aarch64"
    return "qemu-system-x86_64"


def _runtime_dir() -> Path:
    if "XDG_RUNTIME_DIR" in os.environ:
        return Path(os.environ["XDG_RUNTIME_DIR"])
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(f"/run/user/{uid}")


def _user_namespaces_enabled() -> bool:
    path = Path("/proc/sys/user/max_user_namespaces")
    try:
        return int(path.read_text(encoding="utf-8").strip()) > 0
    except (FileNotFoundError, ValueError, OSError):
        return False


def _selinux_enforcing() -> bool:
    path = Path("/sys/fs/selinux/enforce")
    try:
        return path.read_text(encoding="utf-8").strip() == "1"
    except (FileNotFoundError, OSError):
        return False


def _apparmor_enabled() -> bool:
    path = Path("/sys/module/apparmor/parameters/enabled")
    try:
        return path.read_text(encoding="utf-8").strip().upper().startswith("Y")
    except (FileNotFoundError, OSError):
        return False


def _to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {
            definition.name: _to_primitive(getattr(value, definition.name))
            for definition in dataclass_fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_primitive(item) for item in value]
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
