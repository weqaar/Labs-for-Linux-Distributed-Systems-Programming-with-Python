"""Validation, planning, and guarded execution for the on-prem cloud."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

ALLOWED_PROGRAMS = frozenset({"ansible-galaxy", "ansible-playbook", "kubectl", "openstack"})
REQUIRED_ARTIFACTS = (
    "ansible/group_vars/all.yml",
    "ansible/inventory.ini",
    "ansible/kolla-inventory",
    "ansible/kubespray-inventory.ini",
    "ansible/requirements.yml",
    "ansible/site.yml",
    "ansible/templates/domain.xml.j2",
    "ansible/templates/meta-data.j2",
    "ansible/templates/network-config.j2",
    "ansible/templates/user-data.j2",
    "kubernetes/helm-values/harbor.yaml",
    "kubernetes/helm-values/ingress-nginx.yaml",
    "kubernetes/helm-values/jaeger.yaml",
    "kubernetes/helm-values/local-path.yaml",
    "kubernetes/helm-values/loki.yaml",
    "kubernetes/helm-values/minio.yaml",
    "kubernetes/helm-values/metallb.yaml",
    "kubernetes/helm-values/otel.yaml",
    "kubernetes/helm-values/postgresql.yaml",
    "kubernetes/helm-values/prometheus.yaml",
    "kubernetes/helm-values/rabbitmq.yaml",
    "kubernetes/helm-values/valkey.yaml",
    "kubernetes/metallb-pool.yaml",
    "kubernetes/loki-object-user.yaml",
    "openstack/globals.yml",
    "openstack/sigraft.yaml",
)
REQUIRED_OPERATOR_ENV = frozenset(
    {
        "KOLLA_PASSWORDS_FILE",
        "OS_CLIENT_CONFIG_FILE",
        "SIGRAFT_IMAGE_PATH",
        "SIGRAFT_IMAGE_SHA256",
        "SIGRAFT_SSH_PRIVATE_KEY_FILE",
        "SIGRAFT_SSH_PUBLIC_KEY",
    }
)
REQUIRED_SECRET_KEYS = frozenset(
    {
        "grafana_admin",
        "harbor_admin",
        "loki_s3_access_key",
        "loki_s3_secret_key",
        "minio_root_password",
        "minio_root_user",
        "postgresql_admin",
        "postgresql_relay",
        "rabbitmq",
        "valkey",
    }
)
PASSWORD_SECRET_KEYS = REQUIRED_SECRET_KEYS - {
    "loki_s3_access_key",
    "minio_root_user",
}
EXPECTED_ENDPOINTS = {
    "jaeger_otlp": "jaeger.observability.svc.cluster.local:4317",
    "jaeger_query": "http://jaeger.observability.svc.cluster.local:16686",
    "loki_otlp": "http://loki-gateway.data.svc.cluster.local/otlp",
    "loki_query": "http://loki-gateway.data.svc.cluster.local",
}
REQUIRED_SERVICES = frozenset(
    {
        "ceph_or_minio",
        "harbor",
        "internal_dns",
        "jaeger",
        "loki",
        "nginx",
        "opentelemetry_collector",
        "postgresql",
        "prometheus_grafana",
        "rabbitmq",
        "valkey",
    }
)
REQUIRED_NETWORKS = frozenset({"management", "provider", "storage", "tenant"})
REQUIRED_ROLES = frozenset({"control", "storage", "worker"})


class ConfigError(ValueError):
    """A configuration cannot safely describe a deployment."""


class CommandRunner(Protocol):
    """Injected process boundary used only after an explicit action."""

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        """Run one command without invoking a shell."""


class SubprocessRunner:
    """Production runner. Tests use a recording implementation instead."""

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        subprocess.run(list(argv), env=dict(env), check=True, shell=False)


@dataclass(frozen=True)
class Requirements:
    """Aggregate host capacity needed by all virtual machines."""

    vcpus: int
    memory_gib: int
    disk_gib: int


@dataclass(frozen=True)
class VirtualMachine:
    """One libvirt virtual machine."""

    name: str
    role: str
    vcpus: int
    memory_gib: int
    disk_gib: int
    address: str
    dns: str


@dataclass(frozen=True)
class DeploymentConfig:
    """Validated deployment input."""

    name: str
    profile: str
    allow_execution: bool
    hardware_virtualization: bool
    host: Requirements
    reserve: Requirements
    machines: tuple[VirtualMachine, ...]
    networks: Mapping[str, str]
    services: frozenset[str]
    identity_domain: str
    artifact_root: Path
    source_path: Path
    secret_env: Mapping[str, str]


@dataclass(frozen=True)
class PlanStep:
    """One deterministic and inspectable deployment operation."""

    phase: int
    name: str
    argv: tuple[str, ...]


def _mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{location} must be a mapping")
    return cast(Mapping[str, Any], value)


def _positive_int(value: object, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{location} must be a positive integer")
    return value


def _requirements(value: object, location: str) -> Requirements:
    item = _mapping(value, location)
    return Requirements(
        _positive_int(item.get("vcpus"), f"{location}.vcpus"),
        _positive_int(item.get("memory_gib"), f"{location}.memory_gib"),
        _positive_int(item.get("disk_gib"), f"{location}.disk_gib"),
    )


def load_config(path: str | Path) -> DeploymentConfig:
    """Load and validate YAML, resolving artifact paths beside the file."""

    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"cannot read configuration: {error}") from error
    root = _mapping(raw, "configuration")
    version = root.get("version")
    if version != 1:
        raise ConfigError("version must be 1")

    profile = root.get("profile")
    if profile not in {"workstation", "complete"}:
        raise ConfigError("profile must be workstation or complete")
    virtualization = root.get("hardware_virtualization")
    if virtualization is not True:
        raise ConfigError("hardware_virtualization must be true")
    allow_execution = root.get("allow_execution", False)
    if not isinstance(allow_execution, bool):
        raise ConfigError("allow_execution must be a boolean")

    host = _requirements(root.get("host_capacity"), "host_capacity")
    reserve = _requirements(root.get("host_reserve"), "host_reserve")
    vm_values = root.get("virtual_machines")
    if not isinstance(vm_values, list) or not vm_values:
        raise ConfigError("virtual_machines must be a non-empty list")
    machines: list[VirtualMachine] = []
    names: set[str] = set()
    for index, value in enumerate(vm_values):
        vm = _mapping(value, f"virtual_machines[{index}]")
        name, role = vm.get("name"), vm.get("role")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ConfigError("virtual machine names must be non-empty and unique")
        if role not in REQUIRED_ROLES:
            raise ConfigError(f"virtual machine {name} has an unsupported role")
        names.add(name)
        machines.append(
            VirtualMachine(
                name,
                role,
                _positive_int(vm.get("vcpus"), f"{name}.vcpus"),
                _positive_int(vm.get("memory_gib"), f"{name}.memory_gib"),
                _positive_int(vm.get("disk_gib"), f"{name}.disk_gib"),
                str(vm.get("address", "")),
                str(vm.get("dns", "")),
            )
        )

    roles = {machine.role for machine in machines}
    if roles != REQUIRED_ROLES:
        raise ConfigError(f"virtual_machines must include roles: {sorted(REQUIRED_ROLES)}")
    if profile == "complete" and len(machines) < 5:
        raise ConfigError("complete profile requires at least five virtual machines")

    network_values = _mapping(root.get("networks"), "networks")
    networks = {str(key): str(value) for key, value in network_values.items()}
    missing_networks = REQUIRED_NETWORKS - networks.keys()
    if missing_networks:
        raise ConfigError(f"missing networks: {sorted(missing_networks)}")
    parsed_networks: list[IPv4Network] = []
    for name, cidr in networks.items():
        try:
            parsed = ip_network(cidr, strict=True)
        except ValueError as error:
            raise ConfigError(f"network {name} has an invalid CIDR: {cidr}") from error
        if not isinstance(parsed, IPv4Network):
            raise ConfigError(f"network {name} must use IPv4")
        parsed_networks.append(parsed)
    for index, network in enumerate(parsed_networks):
        if any(network.overlaps(other) for other in parsed_networks[index + 1 :]):
            raise ConfigError("network CIDRs must not overlap")
    management = ip_network(networks["management"])
    addresses: set[IPv4Address] = set()
    for machine in machines:
        try:
            address = ip_address(machine.address)
            dns = ip_address(machine.dns)
        except ValueError as error:
            raise ConfigError(f"virtual machine {machine.name} has an invalid address") from error
        if not isinstance(address, IPv4Address) or address not in management:
            raise ConfigError(f"virtual machine {machine.name} must be in the management network")
        if not isinstance(dns, IPv4Address) or dns not in management:
            raise ConfigError(
                f"virtual machine {machine.name} DNS must be in the management network"
            )
        if address in addresses:
            raise ConfigError("virtual machine addresses must be unique")
        addresses.add(address)

    service_values = root.get("services")
    if not isinstance(service_values, list) or not all(
        isinstance(item, str) for item in service_values
    ):
        raise ConfigError("services must be a list of names")
    services = frozenset(service_values)
    missing_services = REQUIRED_SERVICES - services
    if missing_services:
        raise ConfigError(f"missing services: {sorted(missing_services)}")

    identity = _mapping(root.get("identity"), "identity")
    domain = identity.get("domain")
    if not isinstance(domain, str) or "." not in domain:
        raise ConfigError("identity.domain must be a DNS domain")
    secret_values = _mapping(root.get("secret_env"), "secret_env")
    secret_env = {str(key): str(value) for key, value in secret_values.items()}
    if set(secret_env) != REQUIRED_SECRET_KEYS:
        missing = sorted(REQUIRED_SECRET_KEYS - secret_env.keys())
        extra = sorted(secret_env.keys() - REQUIRED_SECRET_KEYS)
        raise ConfigError(
            f"secret_env keys do not match required mappings; missing={missing}, extra={extra}"
        )
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]+", value) for value in secret_env.values()):
        raise ConfigError("secret_env values must be environment variable names")
    if len(set(secret_env.values())) != len(secret_env):
        raise ConfigError("secret_env environment variable names must be unique")

    artifact_value = root.get("artifact_root", "../../onprem")
    if not isinstance(artifact_value, str):
        raise ConfigError("artifact_root must be a path")
    artifact_root = (source.parent / artifact_value).resolve()
    config = DeploymentConfig(
        str(root.get("name", "")),
        profile,
        allow_execution,
        virtualization,
        host,
        reserve,
        tuple(machines),
        networks,
        services,
        domain,
        artifact_root,
        source.resolve(),
        secret_env,
    )
    if not re.fullmatch(r"sigraft-[a-z0-9][a-z0-9-]{1,38}", config.name):
        raise ConfigError(
            "name must start with sigraft- and contain only lowercase safe characters"
        )
    needed = calculate_requirements(config)
    if (
        needed.vcpus + reserve.vcpus > host.vcpus
        or needed.memory_gib + reserve.memory_gib > host.memory_gib
        or needed.disk_gib + reserve.disk_gib > host.disk_gib
    ):
        raise ConfigError("host capacity cannot fit virtual machines and host reserve")
    return config


def calculate_requirements(config: DeploymentConfig) -> Requirements:
    """Return the resources assigned to virtual machines."""

    return Requirements(
        sum(machine.vcpus for machine in config.machines),
        sum(machine.memory_gib for machine in config.machines),
        sum(machine.disk_gib for machine in config.machines),
    )


def make_plan(config: DeploymentConfig) -> tuple[PlanStep, ...]:
    """Produce the same ordered plan for the same validated input."""

    inventory = str(config.artifact_root / "ansible" / "inventory.ini")
    playbook = str(config.artifact_root / "ansible" / "site.yml")
    extra_vars = f"@{config.source_path}"
    return (
        PlanStep(
            5,
            "install pinned Ansible collections",
            (
                "ansible-galaxy",
                "collection",
                "install",
                "-r",
                str(config.artifact_root / "ansible" / "requirements.yml"),
            ),
        ),
        PlanStep(
            10,
            "validate host prerequisites and configuration",
            (
                "ansible-playbook",
                "-i",
                inventory,
                playbook,
                "--tags",
                "prerequisites",
                "--extra-vars",
                extra_vars,
            ),
        ),
        PlanStep(
            20,
            "create owned libvirt networks, disks, cloud-init media, and virtual machines",
            (
                "ansible-playbook",
                "-i",
                inventory,
                playbook,
                "--tags",
                "infrastructure",
                "--extra-vars",
                extra_vars,
            ),
        ),
        PlanStep(
            30,
            "run Kolla bootstrap, prechecks, deploy, and post-deploy",
            (
                "ansible-playbook",
                "-i",
                inventory,
                playbook,
                "--tags",
                "openstack",
                "--extra-vars",
                extra_vars,
            ),
        ),
        PlanStep(
            40,
            "create the bounded OpenStack project resources",
            (
                "openstack",
                "--os-cloud",
                "kolla-admin",
                "stack",
                "create",
                "-t",
                str(config.artifact_root / "openstack" / "sigraft.yaml"),
                config.name,
            ),
        ),
        PlanStep(
            50,
            "install Kubernetes with pinned Kubespray",
            (
                "ansible-playbook",
                "-i",
                inventory,
                playbook,
                "--tags",
                "kubernetes",
                "--extra-vars",
                extra_vars,
            ),
        ),
        PlanStep(
            60,
            "install pinned registry, ingress, data, and observability charts",
            (
                "ansible-playbook",
                "-i",
                inventory,
                playbook,
                "--tags",
                "platform",
                "--extra-vars",
                extra_vars,
            ),
        ),
        PlanStep(
            70,
            "validate OpenStack, Kubernetes, storage, ingress, and telemetry",
            (
                "ansible-playbook",
                "-i",
                inventory,
                playbook,
                "--tags",
                "validate",
                "--extra-vars",
                extra_vars,
            ),
        ),
    )


def validate_artifacts(config: DeploymentConfig) -> None:
    """Reject an apply whose reviewed deployment inputs are absent."""

    missing = [
        relative
        for relative in REQUIRED_ARTIFACTS
        if not (config.artifact_root / relative).is_file()
    ]
    if missing:
        raise ConfigError(f"missing deployment artifacts: {missing}")
    _validate_named_endpoints(config)


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"cannot validate artifact {path}: {error}") from error


def _validate_named_endpoints(config: DeploymentConfig) -> None:
    values = config.artifact_root / "kubernetes" / "helm-values"
    group_vars = _load_yaml(config.artifact_root / "ansible" / "group_vars" / "all.yml")
    charts = group_vars.get("observability_charts")
    if not isinstance(charts, list):
        raise ConfigError("observability_charts must be a list")
    releases = {
        str(_mapping(chart, "observability chart").get("name")): _mapping(
            chart, "observability chart"
        ).get("namespace")
        for chart in charts
    }
    if releases.get("jaeger") != "observability" or releases.get("loki") != "data":
        raise ConfigError("chart release names do not match the validated telemetry endpoints")
    otel = _load_yaml(values / "otel.yaml")
    exporters = _mapping(_mapping(otel.get("config"), "otel.config").get("exporters"), "exporters")
    jaeger = _mapping(exporters.get("otlp/jaeger"), "otlp/jaeger").get("endpoint")
    loki = _mapping(exporters.get("otlphttp/loki"), "otlphttp/loki").get("endpoint")
    if jaeger != EXPECTED_ENDPOINTS["jaeger_otlp"] or loki != EXPECTED_ENDPOINTS["loki_otlp"]:
        raise ConfigError("collector endpoints do not match the pinned Jaeger and Loki services")
    pipelines = _mapping(
        _mapping(_mapping(otel.get("config"), "otel.config").get("service"), "otel.service").get(
            "pipelines"
        ),
        "otel.pipelines",
    )
    log_exporters = _mapping(pipelines.get("logs"), "otel.logs").get("exporters")
    if log_exporters != ["otlphttp/loki"]:
        raise ConfigError("collector logs must use Loki native OTLP HTTP")
    prometheus = _load_yaml(values / "prometheus.yaml")
    grafana = _mapping(prometheus.get("grafana"), "prometheus.grafana")
    sources = grafana.get("additionalDataSources")
    if not isinstance(sources, list):
        raise ConfigError("Grafana data sources must be a list")
    urls = {
        str(_mapping(item, "Grafana data source").get("name")): _mapping(
            item, "Grafana data source"
        ).get("url")
        for item in sources
    }
    if (
        urls.get("Loki") != EXPECTED_ENDPOINTS["loki_query"]
        or urls.get("Jaeger") != EXPECTED_ENDPOINTS["jaeger_query"]
    ):
        raise ConfigError("Grafana endpoints do not match the pinned Jaeger and Loki services")
    loki_values = _load_yaml(values / "loki.yaml")
    loki_storage = _mapping(
        _mapping(_mapping(loki_values.get("loki"), "loki").get("storage"), "loki.storage").get(
            "s3"
        ),
        "loki.storage.s3",
    )
    if (
        loki_storage.get("accessKeyId") != "${AWS_ACCESS_KEY_ID}"
        or loki_storage.get("secretAccessKey") != "${AWS_SECRET_ACCESS_KEY}"
    ):
        raise ConfigError("Loki object storage must use its dedicated secret mapping")


def validate_environment(config: DeploymentConfig, env: Mapping[str, str]) -> None:
    """Require every operator input and distinct per-service secret values."""

    required_names = REQUIRED_OPERATOR_ENV | set(config.secret_env.values())
    missing = sorted(name for name in required_names if not env.get(name))
    if missing:
        raise ConfigError(f"missing required environment inputs: {missing}")
    short = sorted(
        config.secret_env[key]
        for key in PASSWORD_SECRET_KEYS
        if len(env[config.secret_env[key]]) < 16
    )
    if short:
        raise ConfigError(f"password inputs must contain at least 16 characters: {short}")
    password_values = [env[config.secret_env[key]] for key in sorted(PASSWORD_SECRET_KEYS)]
    if len(set(password_values)) != len(password_values):
        raise ConfigError("per-service password inputs must have distinct values")


def _execute(
    config: DeploymentConfig,
    steps: Sequence[PlanStep],
    runner: CommandRunner,
    confirmation: str,
    expected: str,
) -> None:
    if not config.allow_execution:
        raise ConfigError("execution is disabled by configuration")
    if confirmation != expected:
        raise ConfigError(f"confirmation must be exactly {expected}")
    if expected == "APPLY":
        validate_artifacts(config)
    env = dict(os.environ)
    if expected == "APPLY":
        validate_environment(config, env)
    for step in steps:
        if not step.argv or step.argv[0] not in ALLOWED_PROGRAMS:
            raise ConfigError(f"command is not allowlisted: {step.argv!r}")
        runner.run(step.argv, env)


def apply(config: DeploymentConfig, runner: CommandRunner, confirmation: str) -> None:
    """Execute a plan only after both configuration and caller opt in."""

    _execute(config, make_plan(config), runner, confirmation, "APPLY")


def destroy(config: DeploymentConfig, runner: CommandRunner, confirmation: str) -> None:
    """Tear down in dependency order after an exact confirmation."""

    steps = (
        PlanStep(
            10,
            "remove owned platform releases and Kubernetes state",
            (
                "ansible-playbook",
                "-i",
                str(config.artifact_root / "ansible" / "inventory.ini"),
                str(config.artifact_root / "ansible" / "site.yml"),
                "--tags",
                "destroy-platform",
                "--extra-vars",
                f"@{config.source_path}",
            ),
        ),
        PlanStep(
            20,
            "remove OpenStack project resources",
            (
                "openstack",
                "--os-cloud",
                "kolla-admin",
                "stack",
                "delete",
                "--yes",
                "--wait",
                config.name,
            ),
        ),
        PlanStep(
            30,
            "remove only resources carrying the configured ownership prefix",
            (
                "ansible-playbook",
                "-i",
                str(config.artifact_root / "ansible" / "inventory.ini"),
                str(config.artifact_root / "ansible" / "site.yml"),
                "--tags",
                "destroy-infrastructure",
                "--extra-vars",
                f"@{config.source_path}",
            ),
        ),
    )
    _execute(config, steps, runner, confirmation, "DESTROY")
