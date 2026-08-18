"""Offline validation for the relay container deployment checkpoint."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import yaml

_IMAGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9][a-z0-9./-]*@[a-z0-9]+:[0-9a-f]{64}$"
)
_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(connection[_-]?string|account[_-]?key|client[_-]?secret|password|sas)",
    re.IGNORECASE,
)


class ArtifactValidationError(ValueError):
    """Raised when a deployment artifact breaks a required invariant."""


@dataclass(frozen=True)
class DeploymentCheckpoint:
    """All artifacts required for the relay deployment checkpoint."""

    dockerfile_path: Path
    manifest_path: Path
    rollout_path: Path
    dockerfile: str
    service_account: dict[str, Any]
    deployment: dict[str, Any]
    rollout: dict[str, Any]

    @property
    def image(self) -> str:
        containers = cast(
            list[dict[str, Any]], self.deployment["spec"]["template"]["spec"]["containers"]
        )
        return cast(str, containers[0]["image"])


def lab_root() -> Path:
    """Return the lab root directory."""

    return Path(__file__).resolve().parents[2]


def load_checkpoint(root: Path | None = None) -> DeploymentCheckpoint:
    """Load and validate the deployment checkpoint from *root*."""

    actual_root = root if root is not None else lab_root()
    dockerfile_path = actual_root / "Dockerfile"
    manifest_path = actual_root / "deploy/relay-deployment.yaml"
    rollout_path = actual_root / "deploy/rollout-checkpoint.yaml"

    dockerfile = dockerfile_path.read_text(encoding="utf-8")
    manifest_documents = list(yaml.safe_load_all(manifest_path.read_text(encoding="utf-8")))
    if len(manifest_documents) != 2:
        raise ArtifactValidationError(
            "deployment manifest must contain a service account and a deployment"
        )

    service_account = _expect_mapping(manifest_documents[0], "service account")
    deployment = _expect_mapping(manifest_documents[1], "deployment")
    rollout = _expect_mapping(yaml.safe_load(rollout_path.read_text(encoding="utf-8")), "rollout")

    checkpoint = DeploymentCheckpoint(
        dockerfile_path=dockerfile_path,
        manifest_path=manifest_path,
        rollout_path=rollout_path,
        dockerfile=dockerfile,
        service_account=service_account,
        deployment=deployment,
        rollout=rollout,
    )
    validate_checkpoint(checkpoint)
    return checkpoint


def validate_checkpoint(checkpoint: DeploymentCheckpoint) -> None:
    """Validate every required deployment invariant."""

    _validate_dockerfile(checkpoint.dockerfile)
    _validate_service_account(checkpoint.service_account)
    _validate_deployment(checkpoint.deployment, checkpoint.service_account)
    _validate_rollout(checkpoint.rollout, checkpoint.image)


def _validate_dockerfile(dockerfile: str) -> None:
    lines = [line.strip() for line in dockerfile.splitlines() if line.strip()]
    from_lines = [line for line in lines if line.startswith("FROM ")]
    if len(from_lines) < 2:
        raise ArtifactValidationError("Dockerfile must be multi-stage")
    if not any(" as build" in line.casefold() for line in from_lines):
        raise ArtifactValidationError("Dockerfile must name a build stage")
    if not any(" as runtime" in line.casefold() for line in from_lines):
        raise ArtifactValidationError("Dockerfile must name a runtime stage")

    metadata_copy = _find_line(lines, r"^COPY pyproject\.toml README\.md \./$")
    source_copy = _find_line(lines, r"^COPY src ./src$")
    install_line = _find_line(lines, r"^RUN /opt/relay-venv/bin/pip install --no-deps \.$")
    if metadata_copy > source_copy:
        raise ArtifactValidationError("Dockerfile must copy metadata before source for cache reuse")
    if source_copy > install_line:
        raise ArtifactValidationError("Dockerfile must install the package after copying source")
    if not any(line.startswith("USER ") and "root" not in line.casefold() for line in lines):
        raise ArtifactValidationError("Dockerfile must run as a non-root user")
    if 'CMD ["python", "-m", "lab_27_container_deploy.relay_api"]' not in dockerfile:
        raise ArtifactValidationError("Dockerfile must run the relay REST service")
    if _SECRET_PATTERN.search(dockerfile):
        raise ArtifactValidationError("Dockerfile must not bake credentials into any layer")


def _validate_service_account(service_account: dict[str, Any]) -> None:
    if service_account.get("kind") != "ServiceAccount":
        raise ArtifactValidationError("first manifest document must be a ServiceAccount")
    metadata = _expect_mapping(service_account.get("metadata"), "service account metadata")
    annotations = _expect_mapping(metadata.get("annotations"), "service account annotations")
    client_id = annotations.get("azure.workload.identity/client-id")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ArtifactValidationError(
            "service account must declare an Azure workload identity client id"
        )
    if _contains_secret_reference(service_account):
        raise ArtifactValidationError("service account must not depend on Kubernetes secrets")


def _validate_deployment(deployment: dict[str, Any], service_account: dict[str, Any]) -> None:
    if deployment.get("kind") != "Deployment":
        raise ArtifactValidationError("second manifest document must be a Deployment")

    spec = _expect_mapping(deployment.get("spec"), "deployment spec")
    strategy = _expect_mapping(spec.get("strategy"), "deployment strategy")
    if strategy.get("type") != "RollingUpdate":
        raise ArtifactValidationError("deployment must use a rolling update strategy")
    rolling = _expect_mapping(strategy.get("rollingUpdate"), "rolling update strategy")
    if str(rolling.get("maxUnavailable")) != "0":
        raise ArtifactValidationError("rolling updates must keep every replica serving")
    if str(rolling.get("maxSurge")) != "1":
        raise ArtifactValidationError("rolling updates must only add one extra pod at a time")

    template = _expect_mapping(spec.get("template"), "deployment template")
    template_metadata = _expect_mapping(template.get("metadata"), "template metadata")
    labels = _expect_mapping(template_metadata.get("labels"), "template labels")
    if labels.get("azure.workload.identity/use") != "true":
        raise ArtifactValidationError("pods must opt in to Azure workload identity")

    pod_spec = _expect_mapping(template.get("spec"), "pod spec")
    service_account_name = _expect_mapping(service_account.get("metadata"), "metadata").get("name")
    if pod_spec.get("serviceAccountName") != service_account_name:
        raise ArtifactValidationError("deployment must use the managed identity service account")
    pod_security = _expect_mapping(pod_spec.get("securityContext"), "pod security context")
    if pod_security.get("runAsNonRoot") is not True:
        raise ArtifactValidationError("pod security context must enforce non-root execution")
    if int(pod_security.get("runAsUser", 0)) <= 0:
        raise ArtifactValidationError("pod security context must pin a non-root uid")

    termination_grace = int(pod_spec.get("terminationGracePeriodSeconds", 0))
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise ArtifactValidationError("deployment must define exactly one relay container")
    container = _expect_mapping(containers[0], "relay container")
    image = container.get("image")
    if not isinstance(image, str) or _IMAGE_PATTERN.match(image) is None:
        raise ArtifactValidationError("container image must be pinned by digest")

    container_security = _expect_mapping(
        container.get("securityContext"), "container security context"
    )
    if container_security.get("allowPrivilegeEscalation") is not False:
        raise ArtifactValidationError("container must disable privilege escalation")
    if container_security.get("readOnlyRootFilesystem") is not True:
        raise ArtifactValidationError("container must use a read-only root filesystem")

    env_entries = container.get("env")
    if not isinstance(env_entries, list):
        raise ArtifactValidationError("container must define its environment explicitly")
    environment = _env_map(env_entries)
    if "AZURE_CLIENT_ID" not in environment:
        raise ArtifactValidationError("container must use a managed identity client id")
    drain_seconds = int(environment.get("RELAY_DRAIN_SECONDS", "0"))
    if termination_grace <= drain_seconds:
        raise ArtifactValidationError("termination grace period must exceed the drain period")
    if _contains_secret_reference(container):
        raise ArtifactValidationError(
            "container configuration must not pull credentials from secrets"
        )

    readiness = _probe_path(container, "readinessProbe")
    liveness = _probe_path(container, "livenessProbe")
    if {readiness, liveness} != {"/readyz", "/livez"}:
        raise ArtifactValidationError("readiness and liveness must use distinct health paths")

    resources = _expect_mapping(container.get("resources"), "container resources")
    requests = _expect_mapping(resources.get("requests"), "resource requests")
    limits = _expect_mapping(resources.get("limits"), "resource limits")
    for key in ("cpu", "memory"):
        if key not in requests or key not in limits:
            raise ArtifactValidationError("resources must set cpu and memory requests and limits")


def _validate_rollout(rollout: dict[str, Any], deployment_image: str) -> None:
    if rollout.get("kind") != "RelayRolloutCheckpoint":
        raise ArtifactValidationError("rollout artifact must be a RelayRolloutCheckpoint")
    spec = _expect_mapping(rollout.get("spec"), "rollout spec")
    image_name = spec.get("image")
    current_digest = spec.get("currentDigest")
    previous_digest = spec.get("previousDigest")
    rollback_command = spec.get("rollbackCommand")
    strategy = _expect_mapping(spec.get("strategy"), "rollout strategy")

    if not isinstance(image_name, str) or not image_name.strip():
        raise ArtifactValidationError("rollout artifact must declare the image name")
    if not isinstance(current_digest, str) or _DIGEST_PATTERN.match(current_digest) is None:
        raise ArtifactValidationError("rollout artifact must declare the current digest")
    if not isinstance(previous_digest, str) or _DIGEST_PATTERN.match(previous_digest) is None:
        raise ArtifactValidationError("rollout artifact must declare the previous digest")
    if current_digest == previous_digest:
        raise ArtifactValidationError("rollout artifact must keep the previous digest for rollback")
    expected_image = f"{image_name}@{current_digest}"
    if deployment_image != expected_image:
        raise ArtifactValidationError("rollout current digest must match the deployment image")
    if strategy.get("type") != "RollingUpdate":
        raise ArtifactValidationError("rollout artifact must describe the rolling update strategy")
    if str(strategy.get("maxUnavailable")) != "0" or str(strategy.get("maxSurge")) != "1":
        raise ArtifactValidationError("rollout strategy must preserve service during changes")
    if not isinstance(rollback_command, str) or previous_digest not in rollback_command:
        raise ArtifactValidationError("rollback command must name the previous digest")
    if "deployment/relay" not in rollback_command:
        raise ArtifactValidationError("rollback command must target the relay deployment")


def _expect_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{label} must be a mapping")
    return cast(dict[str, Any], value)


def _find_line(lines: list[str], pattern: str) -> int:
    compiled = re.compile(pattern)
    for index, line in enumerate(lines):
        if compiled.match(line):
            return index
    raise ArtifactValidationError(f"Dockerfile is missing required line: {pattern}")


def _env_map(entries: list[Any]) -> dict[str, str]:
    env_map: dict[str, str] = {}
    for raw_entry in entries:
        entry = _expect_mapping(raw_entry, "environment entry")
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise ArtifactValidationError("environment entries must use explicit string values")
        if _SECRET_PATTERN.search(name) or _SECRET_PATTERN.search(value):
            raise ArtifactValidationError("environment must not contain secret material")
        env_map[name] = value
    return env_map


def _probe_path(container: dict[str, Any], probe_name: str) -> str:
    probe = _expect_mapping(container.get(probe_name), probe_name)
    http_get = _expect_mapping(probe.get("httpGet"), f"{probe_name} httpGet")
    path = http_get.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ArtifactValidationError(f"{probe_name} must use an absolute HTTP path")
    return path


def _contains_secret_reference(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "secretKeyRef":
                return True
            if _contains_secret_reference(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret_reference(child) for child in value)
    if isinstance(value, str):
        return _SECRET_PATTERN.search(value) is not None
    return False


def main() -> None:  # pragma: no cover
    checkpoint = load_checkpoint()
    print(checkpoint.image)


if __name__ == "__main__":  # pragma: no cover
    main()
