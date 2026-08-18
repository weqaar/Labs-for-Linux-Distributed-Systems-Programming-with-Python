"""Tests for the relay deployment checkpoint."""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from lab_27_container_deploy import __version__, load_checkpoint, validate_checkpoint
from lab_27_container_deploy.checkpoint import (
    ArtifactValidationError,
    DeploymentCheckpoint,
    lab_root,
)
from lab_27_container_deploy.relay_api import RelayApplication, build_handler


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
    broken = DeploymentCheckpoint(
        dockerfile_path=checkpoint.dockerfile_path,
        manifest_path=checkpoint.manifest_path,
        rollout_path=checkpoint.rollout_path,
        dockerfile=checkpoint.dockerfile,
        service_account=checkpoint.service_account,
        deployment=checkpoint.deployment,
        rollout=rollout,
    )

    with pytest.raises(ArtifactValidationError, match="previous digest"):
        validate_checkpoint(broken)


def test_lab_root_points_at_the_project_directory() -> None:
    assert lab_root() == Path(__file__).resolve().parents[1]


def test_version_is_exposed() -> None:
    assert __version__
