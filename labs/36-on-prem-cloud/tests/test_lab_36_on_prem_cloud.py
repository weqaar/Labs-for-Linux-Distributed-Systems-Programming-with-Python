"""Tests prove that planning is safe, deterministic, and strict."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
import yaml

from lab_36_on_prem_cloud import (
    ConfigError,
    DeploymentConfig,
    apply,
    destroy,
    load_config,
    make_plan,
    validate_artifacts,
    validate_environment,
)
from lab_36_on_prem_cloud.__main__ import main
from lab_36_on_prem_cloud.infrastructure import (
    REQUIRED_ARTIFACTS,
    calculate_requirements,
)


def valid_config() -> dict[str, object]:
    return {
        "version": 1,
        "name": "sigraft-test",
        "profile": "complete",
        "allow_execution": False,
        "hardware_virtualization": True,
        "host_capacity": {"vcpus": 32, "memory_gib": 128, "disk_gib": 2048},
        "host_reserve": {"vcpus": 4, "memory_gib": 16, "disk_gib": 200},
        "virtual_machines": [
            {
                "name": "control-1",
                "role": "control",
                "address": "10.70.0.11",
                "dns": "10.70.0.1",
                "vcpus": 4,
                "memory_gib": 16,
                "disk_gib": 160,
            },
            {
                "name": "control-2",
                "role": "control",
                "address": "10.70.0.12",
                "dns": "10.70.0.1",
                "vcpus": 4,
                "memory_gib": 16,
                "disk_gib": 160,
            },
            {
                "name": "worker-1",
                "role": "worker",
                "address": "10.70.0.21",
                "dns": "10.70.0.1",
                "vcpus": 6,
                "memory_gib": 24,
                "disk_gib": 240,
            },
            {
                "name": "worker-2",
                "role": "worker",
                "address": "10.70.0.22",
                "dns": "10.70.0.1",
                "vcpus": 6,
                "memory_gib": 24,
                "disk_gib": 240,
            },
            {
                "name": "storage-1",
                "role": "storage",
                "address": "10.70.0.31",
                "dns": "10.70.0.1",
                "vcpus": 4,
                "memory_gib": 24,
                "disk_gib": 800,
            },
        ],
        "networks": {
            "management": "10.70.0.0/24",
            "provider": "10.70.1.0/24",
            "storage": "10.70.2.0/24",
            "tenant": "10.70.3.0/24",
        },
        "services": [
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
        ],
        "identity": {"domain": "home.sigraft.test"},
        "secret_env": {
            "harbor_admin": "SIGRAFT_HARBOR_ADMIN_PASSWORD",
            "postgresql_admin": "SIGRAFT_POSTGRES_ADMIN_PASSWORD",
            "postgresql_relay": "SIGRAFT_POSTGRES_RELAY_PASSWORD",
            "rabbitmq": "SIGRAFT_RABBITMQ_PASSWORD",
            "valkey": "SIGRAFT_VALKEY_PASSWORD",
            "minio_root_user": "SIGRAFT_MINIO_ROOT_USER",
            "minio_root_password": "SIGRAFT_MINIO_ROOT_PASSWORD",
            "grafana_admin": "SIGRAFT_GRAFANA_ADMIN_PASSWORD",
            "loki_s3_access_key": "SIGRAFT_LOKI_S3_ACCESS_KEY",
            "loki_s3_secret_key": "SIGRAFT_LOKI_S3_SECRET_KEY",
        },
        "artifact_root": ".",
    }


def write_config(tmp_path: Path, values: dict[str, object]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def create_artifacts(tmp_path: Path) -> None:
    for relative in REQUIRED_ARTIFACTS:
        artifact = tmp_path / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("reviewed: true\n", encoding="utf-8")
    values = tmp_path / "kubernetes" / "helm-values"
    (tmp_path / "ansible" / "group_vars" / "all.yml").write_text(
        yaml.safe_dump(
            {
                "observability_charts": [
                    {"name": "jaeger", "namespace": "observability"},
                    {"name": "loki", "namespace": "data"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (values / "otel.yaml").write_text(
        yaml.safe_dump(
            {
                "config": {
                    "exporters": {
                        "otlp/jaeger": {"endpoint": "jaeger.observability.svc.cluster.local:4317"},
                        "otlphttp/loki": {
                            "endpoint": "http://loki-gateway.data.svc.cluster.local/otlp"
                        },
                    },
                    "service": {"pipelines": {"logs": {"exporters": ["otlphttp/loki"]}}},
                }
            }
        ),
        encoding="utf-8",
    )
    (values / "loki.yaml").write_text(
        yaml.safe_dump(
            {
                "loki": {
                    "storage": {
                        "s3": {
                            "accessKeyId": "${AWS_ACCESS_KEY_ID}",
                            "secretAccessKey": "${AWS_SECRET_ACCESS_KEY}",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (values / "prometheus.yaml").write_text(
        yaml.safe_dump(
            {
                "grafana": {
                    "additionalDataSources": [
                        {
                            "name": "Loki",
                            "url": "http://loki-gateway.data.svc.cluster.local",
                        },
                        {
                            "name": "Jaeger",
                            "url": "http://jaeger.observability.svc.cluster.local:16686",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def operator_env(config: DeploymentConfig) -> dict[str, str]:
    secret_env = config.secret_env
    env = {
        "KOLLA_PASSWORDS_FILE": "/secure/kolla.yml",
        "OS_CLIENT_CONFIG_FILE": "/etc/kolla/clouds.yaml",
        "SIGRAFT_IMAGE_PATH": "/images/ubuntu.qcow2",
        "SIGRAFT_IMAGE_SHA256": "a" * 64,
        "SIGRAFT_SSH_PRIVATE_KEY_FILE": "/secure/id_ed25519",
        "SIGRAFT_SSH_PUBLIC_KEY": "ssh-ed25519 " + "a" * 48,
    }
    for index, name in enumerate(secret_env.values()):
        env[name] = f"distinct-secret-{index:02d}-value"
    return env


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        assert env
        self.calls.append(tuple(argv))


def test_loads_and_calculates_aggregate(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config()))
    required = calculate_requirements(config)
    assert (required.vcpus, required.memory_gib, required.disk_gib) == (24, 104, 1600)


def test_plan_is_ordered_and_deterministic(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config()))
    first = make_plan(config)
    assert first == make_plan(config)
    assert [step.phase for step in first] == sorted(step.phase for step in first)
    assert first[0].argv[0] == "ansible-galaxy"
    assert first[-1].argv[0] == "ansible-playbook"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"hardware_virtualization": False}, "hardware_virtualization"),
        ({"services": ["nginx"]}, "missing services"),
        ({"networks": {}}, "missing networks"),
        (
            {
                "networks": {
                    "management": "not-a-cidr",
                    "provider": "10.70.1.0/24",
                    "storage": "10.70.2.0/24",
                    "tenant": "10.70.3.0/24",
                }
            },
            "invalid CIDR",
        ),
        (
            {
                "networks": {
                    "management": "10.70.0.0/24",
                    "provider": "10.70.0.128/25",
                    "storage": "10.70.2.0/24",
                    "tenant": "10.70.3.0/24",
                }
            },
            "must not overlap",
        ),
        ({"host_capacity": {"vcpus": 4, "memory_gib": 8, "disk_gib": 20}}, "cannot fit"),
        ({"profile": "complete", "virtual_machines": []}, "non-empty"),
        ({"secret_env": {}}, "keys do not match"),
    ],
)
def test_rejects_incomplete_or_unsafe_config(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    values = valid_config()
    values.update(change)
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, values))


def test_plan_never_runs_commands(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config()))
    recorder = Recorder()
    make_plan(config)
    assert recorder.calls == []


def test_command_line_defaults_to_a_read_only_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_config(tmp_path, valid_config())
    monkeypatch.setattr("sys.argv", ["planner", str(path)])
    main()
    output = capsys.readouterr().out
    assert "VM requirement: 24 vCPU" in output
    assert "05 install pinned Ansible collections" in output


def test_apply_requires_two_explicit_opt_ins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, valid_config())
    recorder = Recorder()
    with pytest.raises(ConfigError, match="disabled"):
        apply(load_config(path), recorder, "APPLY")
    values = valid_config()
    values["allow_execution"] = True
    enabled = load_config(write_config(tmp_path, values))
    with pytest.raises(ConfigError, match="exactly APPLY"):
        apply(enabled, recorder, "")
    with pytest.raises(ConfigError, match="missing deployment artifacts"):
        apply(enabled, recorder, "APPLY")
    create_artifacts(tmp_path)
    for name, value in operator_env(enabled).items():
        monkeypatch.setenv(name, value)
    apply(enabled, recorder, "APPLY")
    assert recorder.calls
    assert {call[0] for call in recorder.calls} <= {
        "ansible-playbook",
        "openstack",
        "kubectl",
        "ansible-galaxy",
    }


def test_destroy_requires_exact_confirmation_and_is_ordered(tmp_path: Path) -> None:
    values = valid_config()
    values["allow_execution"] = True
    config = load_config(write_config(tmp_path, values))
    recorder = Recorder()
    with pytest.raises(ConfigError, match="exactly DESTROY"):
        destroy(config, recorder, "destroy")
    destroy(config, recorder, "DESTROY")
    assert [call[0] for call in recorder.calls] == [
        "ansible-playbook",
        "openstack",
        "ansible-playbook",
    ]


def test_rejects_missing_and_reused_service_secrets(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config()))
    with pytest.raises(ConfigError, match="missing required environment"):
        validate_environment(config, {})
    env = operator_env(config)
    env["SIGRAFT_HARBOR_ADMIN_PASSWORD"] = env["SIGRAFT_RABBITMQ_PASSWORD"]
    with pytest.raises(ConfigError, match="distinct"):
        validate_environment(config, env)


def test_rejects_mismatched_named_endpoints(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config()))
    create_artifacts(tmp_path)
    values = tmp_path / "kubernetes" / "helm-values" / "otel.yaml"
    document = yaml.safe_load(values.read_text(encoding="utf-8"))
    document["config"]["exporters"]["otlp/jaeger"]["endpoint"] = "wrong:4317"
    values.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ConfigError, match="collector endpoints"):
        validate_artifacts(config)
