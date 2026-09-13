"""Tests for the relay deployment checkpoint."""

from __future__ import annotations

import json
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from lab_32_container_deploy import __version__, load_checkpoint, validate_checkpoint
from lab_32_container_deploy.checkpoint import (
    ArtifactValidationError,
    lab_root,
)
from lab_32_container_deploy.relay_api import RelayApplication, build_handler
from lab_32_container_deploy.sdk_adapters import (
    promote_relay,
    run_relay_smoke_check,
    watch_relay_rollout,
)


class FakeContainer:
    def __init__(self, *, status: int = 0) -> None:
        self.id = "container-27"
        self.status = status
        self.removed = False

    def wait(self, *, timeout: int) -> dict[str, object]:
        assert timeout == 30
        return {"StatusCode": self.status}

    def logs(self) -> bytes:
        return b"relayctl 0.1.0\n"

    def remove(self, *, force: bool = False) -> None:
        self.removed = force


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.arguments: tuple[tuple[Any, ...], dict[str, Any]] | None = None

    def run(self, *args: Any, **kwargs: Any) -> FakeContainer:
        self.arguments = (args, kwargs)
        return self.container


class FakeDockerClient:
    def __init__(self, container: FakeContainer) -> None:
        self.containers = FakeContainers(container)


class FakeAppsApi:
    def __init__(self) -> None:
        self.patch: dict[str, object] | None = None

    def patch_namespaced_deployment(self, **kwargs: object) -> object:
        self.patch = kwargs
        return {}

    def list_namespaced_deployment(
        self, namespace: str, *, field_selector: str, timeout_seconds: int
    ) -> object:
        return namespace, field_selector, timeout_seconds


class FakeWatch:
    def stream(
        self,
        function: object,
        namespace: str,
        *,
        field_selector: str,
        timeout_seconds: int,
    ) -> list[dict[str, object]]:
        assert callable(function)
        assert (namespace, field_selector, timeout_seconds) == (
            "relay",
            "metadata.name=relay",
            60,
        )
        return [
            {
                "type": "MODIFIED",
                "object": {
                    "metadata": {"generation": 4},
                    "spec": {"replicas": 3},
                    "status": {
                        "observed_generation": 4,
                        "available_replicas": 3,
                        "updated_replicas": 3,
                    },
                },
            }
        ]


def test_checkpoint_artifacts_validate_and_pin_a_digest() -> None:
    checkpoint = load_checkpoint()

    assert checkpoint.image == (
        "relay.azurecr.io/relay@sha256:0123456789abcdef0123456789abcdef"
        "0123456789abcdef0123456789abcdef"
    )
    assert checkpoint.rollout["spec"]["rollbackCommand"].endswith(
        "sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
    )


def test_deployment_splits_health_paths_and_grace_period() -> None:
    checkpoint = load_checkpoint()
    container = checkpoint.deployment["spec"]["template"]["spec"]["containers"][0]
    env_values = {entry["name"]: entry["value"] for entry in container["env"]}

    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/livez"
    assert container["startupProbe"]["httpGet"]["path"] == "/livez"
    assert checkpoint.deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] > int(
        env_values["RELAY_DRAIN_SECONDS"]
    )


def test_dockerfile_keeps_metadata_before_source_for_cache_reuse() -> None:
    dockerfile = (lab_root() / "Dockerfile").read_text(encoding="utf-8").splitlines()

    metadata_index = dockerfile.index("COPY pyproject.toml README.md ./")
    source_index = dockerfile.index("COPY src ./src")
    install_index = dockerfile.index("RUN /opt/relay-venv/bin/pip install --no-deps .")

    assert metadata_index < source_index < install_index
    assert any(line.startswith("USER relay") for line in dockerfile)


def test_service_ingress_scaling_disruption_and_failure_domains_are_explicit() -> None:
    checkpoint = load_checkpoint()

    assert checkpoint.service["spec"]["type"] == "ClusterIP"
    assert checkpoint.ingress["spec"]["ingressClassName"] == "nginx"
    assert checkpoint.autoscaler["spec"]["minReplicas"] == 2
    assert checkpoint.disruption_budget["spec"]["minAvailable"] == 1
    assert checkpoint.deployment["spec"]["template"]["spec"]["topologySpreadConstraints"]


def test_canary_blue_green_and_environment_boundaries_are_executable_artifacts() -> None:
    checkpoint = load_checkpoint()
    canary_ingress = checkpoint.canary[2]
    namespaces = {
        document["metadata"]["name"]
        for document in checkpoint.environments
        if document["kind"] == "Namespace"
    }

    assert (
        canary_ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/canary-weight"]
        == "10"
    )
    assert namespaces == {"relay-staging", "relay-prod"}
    assert set(checkpoint.release_strategies["spec"]) == {
        "rolling",
        "canary",
        "blueGreen",
    }
    assert checkpoint.kind_cluster["nodes"][1]["role"] == "worker"


def test_relay_application_handles_health_and_task_submission() -> None:
    application = RelayApplication()

    assert application.handle_request("GET", "/livez") == (200, {"status": "alive"})
    assert application.handle_request("GET", "/readyz") == (200, {"status": "ready"})
    assert application.handle_request("POST", "/tasks", {"task": "ship checkpoint"}) == (
        202,
        {"task_id": "relay-0001", "task": "ship checkpoint", "state": "accepted"},
    )
    assert application.handle_request("POST", "/tasks", {"task": "   "}) == (
        400,
        {"error": "task is required"},
    )


def test_ready_probe_goes_unready_while_draining() -> None:
    application = RelayApplication(draining=True)

    assert application.handle_request("GET", "/readyz") == (503, {"status": "draining"})


def test_http_handler_serves_requests_over_http() -> None:
    application = RelayApplication()
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(application))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        with urlopen(f"{base_url}/livez", timeout=5) as response:
            assert json.loads(response.read().decode("utf-8")) == {"status": "alive"}

        request = Request(
            f"{base_url}/tasks",
            data=json.dumps({"task": "serve over http"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert json.loads(response.read().decode("utf-8")) == {
                "task_id": "relay-0001",
                "task": "serve over http",
                "state": "accepted",
            }

        with pytest.raises(HTTPError, match="404"):
            urlopen(f"{base_url}/missing", timeout=5)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_validation_rejects_rollout_without_previous_digest() -> None:
    checkpoint = load_checkpoint()
    rollout = {
        **checkpoint.rollout,
        "spec": {
            **checkpoint.rollout["spec"],
            "previousDigest": checkpoint.rollout["spec"]["currentDigest"],
        },
    }
    broken = replace(checkpoint, rollout=rollout)

    with pytest.raises(ArtifactValidationError, match="previous digest"):
        validate_checkpoint(broken)


def test_docker_sdk_smoke_check_is_hardened_and_always_cleaned_up() -> None:
    container = FakeContainer()
    client = FakeDockerClient(container)
    image = "relay.example/relay@sha256:" + "a" * 64

    result = run_relay_smoke_check(client, image)

    assert result.output == "relayctl 0.1.0"
    assert container.removed is True
    assert client.containers.arguments is not None
    _, options = client.containers.arguments
    assert options["read_only"] is True
    assert options["cap_drop"] == ("ALL",)
    assert options["network_disabled"] is True


def test_docker_sdk_failure_surfaces_logs_and_still_cleans_up() -> None:
    container = FakeContainer(status=17)
    client = FakeDockerClient(container)
    image = "relay.example/relay@sha256:" + "c" * 64

    with pytest.raises(RuntimeError, match="exited 17: relayctl 0.1.0"):
        run_relay_smoke_check(client, image)

    assert container.removed is True


def test_kubernetes_sdk_promotes_a_digest_and_watches_readiness() -> None:
    api = FakeAppsApi()
    image = "relay.example/relay@sha256:" + "b" * 64

    promotion = promote_relay(api, image)

    assert promotion.image == image
    assert api.patch is not None
    assert api.patch["field_manager"] == "relay-release"
    assert watch_relay_rollout(api, FakeWatch()) is True


def test_lab_root_points_at_the_project_directory() -> None:
    assert lab_root() == Path(__file__).resolve().parents[1]


def test_version_is_exposed() -> None:
    assert __version__
