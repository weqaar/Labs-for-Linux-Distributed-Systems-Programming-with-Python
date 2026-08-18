"""Tests for the local relay runtime planner."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from lab_27_container_deploy.checkpoint import lab_root
from lab_27_container_deploy.local_runtime import (
    QEMU_DOMAIN_NAMESPACE,
    DockerPlan,
    HostCapabilities,
    IsolationBoundary,
    KataPlan,
    LibvirtPlan,
    LxcPlan,
    QemuAccelerator,
    QemuPlan,
    RelayRuntimeInputs,
    RuntimeKind,
    RuntimeRequirements,
    RuntimeSelectionError,
    define_libvirt_domain,
    main,
    negotiate_qmp,
    plan_runtime,
    start_docker_container,
)


class FakeDockerApi:
    """Deterministic fake Docker client."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

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
    ) -> str:
        self.calls.append(
            (
                "create",
                name,
                image,
                tuple(command),
                user,
                read_only,
                tuple(cap_drop),
                tuple(security_opt),
                dict(tmpfs),
                dict(environment),
                dict(ports),
                pids_limit,
            )
        )
        return "container-1"

    def start_container(self, container_id: str) -> None:
        self.calls.append(("start", container_id))


class FakeLibvirtApi:
    """Deterministic fake libvirt client."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def define_domain(self, connection_uri: str, domain_xml: str) -> str:
        self.calls.append(("define", connection_uri, domain_xml))
        return "relay"

    def start_domain(self, domain_name: str) -> None:
        self.calls.append(("start", domain_name))


class FakeQmpApi:
    """Deterministic fake QMP client."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def connect(self, socket_path: str) -> None:
        self.calls.append(("connect", socket_path))

    def command(self, name: str, arguments: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        normalized = dict(arguments or {})
        self.calls.append(("command", name, normalized))
        if name == "query-status":
            return {"return": {"status": "running"}}
        return {"return": {}}

    def close(self) -> None:
        self.calls.append(("close",))


def test_auto_runtime_falls_back_to_tcg_without_kvm() -> None:
    plan = plan_runtime(
        requirements=RuntimeRequirements(minimum_isolation=IsolationBoundary.VIRTUAL_MACHINE),
        host=HostCapabilities.named_profile("portable"),
    )

    assert plan.runtime is RuntimeKind.QEMU_TCG
    assert plan.launch_ready is True
    assert isinstance(plan.details, QemuPlan)
    assert plan.details.accelerator is QemuAccelerator.TCG
    assert "-accel" in plan.details.argv
    assert "tcg,thread=multi" in plan.details.argv
    assert "-sandbox" in plan.details.argv
    assert plan.details.qmp_socket_path.endswith("relay.qmp.sock")
    assert "slower software emulation" in plan.selection_reason


def test_qemu_kvm_requires_a_kvm_device() -> None:
    with pytest.raises(RuntimeSelectionError, match="/dev/kvm"):
        plan_runtime(RuntimeKind.QEMU_KVM, host=HostCapabilities.named_profile("portable"))


def test_hardware_acceleration_requirement_fails_without_kvm() -> None:
    with pytest.raises(RuntimeSelectionError, match="KVM-backed acceleration"):
        plan_runtime(
            requirements=RuntimeRequirements(
                minimum_isolation=IsolationBoundary.VIRTUAL_MACHINE,
                require_hardware_acceleration=True,
            ),
            host=HostCapabilities.named_profile("portable"),
        )


def test_missing_qemu_binary_is_reported_without_claiming_execution() -> None:
    host = replace(HostCapabilities.named_profile("portable"), qemu_system_path=None)

    plan = plan_runtime(RuntimeKind.QEMU_TCG, host=host)

    assert plan.launch_ready is False
    assert plan.launch_issues == ("qemu-system was not detected on the host",)
    assert isinstance(plan.details, QemuPlan)
    assert plan.details.executable == "qemu-system-x86_64"


def test_docker_rejects_utility_vm_requirement() -> None:
    with pytest.raises(RuntimeSelectionError, match="utility-vm"):
        plan_runtime(
            RuntimeKind.DOCKER,
            requirements=RuntimeRequirements(minimum_isolation=IsolationBoundary.UTILITY_VM),
            host=HostCapabilities.named_profile("containers"),
        )


def test_docker_plan_hardens_the_container() -> None:
    plan = plan_runtime(RuntimeKind.DOCKER, host=HostCapabilities.named_profile("containers"))

    assert isinstance(plan.details, DockerPlan)
    assert plan.details.container.user == "10001:10001"
    assert plan.details.container.read_only is True
    assert plan.details.container.cap_drop == ("ALL",)
    assert plan.details.container.security_opt == ("no-new-privileges",)
    assert plan.details.container.tmpfs["/tmp"].startswith("rw,noexec")
    assert "--read-only" in plan.details.argv
    assert "--cap-drop=ALL" in plan.details.argv


def test_lxc_plan_uses_unprivileged_uid_maps_and_matches_artifact() -> None:
    plan = plan_runtime(
        RuntimeKind.LXC_UNPRIVILEGED,
        host=HostCapabilities.named_profile("containers"),
        inputs=RelayRuntimeInputs(lxc_rootfs_path="/srv/relay/rootfs"),
    )

    assert isinstance(plan.details, LxcPlan)
    assert "lxc.rootfs.options = ro" in plan.details.config_lines
    assert "lxc.idmap = u 0 100000 65536" in plan.details.config_lines
    assert "lxc.idmap = g 0 100000 65536" in plan.details.config_lines
    artifact = (lab_root() / "deploy/relay-local-lxc.conf").read_text(encoding="utf-8").splitlines()
    assert list(plan.details.config_lines) == artifact


def test_lxc_requires_user_namespaces() -> None:
    host = replace(HostCapabilities.named_profile("containers"), user_namespaces=False)

    with pytest.raises(RuntimeSelectionError, match="user namespaces"):
        plan_runtime(RuntimeKind.LXC_UNPRIVILEGED, host=host)


def test_libvirt_plan_uses_session_scoping_svirt_and_sandbox() -> None:
    plan = plan_runtime(RuntimeKind.LIBVIRT_KVM, host=HostCapabilities.named_profile("all"))

    assert isinstance(plan.details, LibvirtPlan)
    root = ET.fromstring(plan.details.domain_xml)
    ns = {"qemu": QEMU_DOMAIN_NAMESPACE}
    qemu_args = [
        element.attrib["value"] for element in root.findall("./qemu:commandline/qemu:arg", ns)
    ]

    assert plan.details.connection_uri == "qemu:///session"
    assert root.attrib["type"] == "kvm"
    seclabel = root.find("./seclabel")
    assert seclabel is not None
    assert seclabel.attrib["type"] == "dynamic"
    assert seclabel.attrib["model"] == "selinux"
    assert root.find("./devices/disk[@device='disk']/readonly") is not None
    assert "-qmp" in qemu_args
    assert "-sandbox" in qemu_args
    assert any(
        argument.startswith("unix:") and argument.endswith("wait=off") for argument in qemu_args
    )


def test_kata_plan_uses_a_runtimeclass_and_matches_the_artifact() -> None:
    plan = plan_runtime(RuntimeKind.KATA, host=HostCapabilities.named_profile("all"))

    assert plan.isolation is IsolationBoundary.UTILITY_VM
    assert isinstance(plan.details, KataPlan)
    assert plan.details.runtime_handler == "kata"
    assert plan.details.pod_patch["spec"]["runtimeClassName"] == "relay-kata"
    assert "utility VM" in plan.details.notes[0]
    artifact = yaml.safe_load(
        (lab_root() / "deploy/relay-kata-runtimeclass.yaml").read_text(encoding="utf-8")
    )
    assert artifact == plan.details.runtime_class


def test_cli_plan_prints_json_for_a_portable_vm_host(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "plan",
            "--runtime",
            "auto",
            "--minimum-isolation",
            "virtual-machine",
            "--host-profile",
            "portable",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["runtime"] == "qemu-tcg"
    assert payload["details"]["accelerator"] == "tcg"
    assert payload["launch_ready"] is True


def test_docker_api_boundary_uses_the_injected_client() -> None:
    plan = plan_runtime(RuntimeKind.DOCKER, host=HostCapabilities.named_profile("containers"))
    api = FakeDockerApi()

    assert isinstance(plan.details, DockerPlan)
    receipt = start_docker_container(plan.details, api)

    assert receipt.identifier == "container-1"
    assert api.calls[0][0] == "create"
    assert api.calls[0][1] == "relay"
    assert api.calls[1] == ("start", "container-1")


def test_libvirt_api_boundary_uses_the_injected_client() -> None:
    plan = plan_runtime(RuntimeKind.LIBVIRT_KVM, host=HostCapabilities.named_profile("all"))
    api = FakeLibvirtApi()

    assert isinstance(plan.details, LibvirtPlan)
    receipt = define_libvirt_domain(plan.details, api)

    assert receipt.identifier == "relay"
    assert api.calls[0][0] == "define"
    assert api.calls[0][1] == "qemu:///session"
    assert api.calls[1] == ("start", "relay")


def test_qmp_api_boundary_uses_the_injected_client() -> None:
    plan = plan_runtime(RuntimeKind.QEMU_TCG, host=HostCapabilities.named_profile("portable"))
    api = FakeQmpApi()

    assert isinstance(plan.details, QemuPlan)
    responses = negotiate_qmp(plan.details, api)

    assert responses[0] == {"return": {}}
    assert responses[1] == {"return": {"status": "running"}}
    assert api.calls[0] == ("connect", plan.details.qmp_socket_path)
    assert api.calls[1] == ("command", "qmp_capabilities", {})
    assert api.calls[2] == ("command", "query-status", {})
    assert api.calls[3] == ("close",)


def test_lab_root_stays_inside_the_lab() -> None:
    assert lab_root() == Path(__file__).resolve().parents[1]
