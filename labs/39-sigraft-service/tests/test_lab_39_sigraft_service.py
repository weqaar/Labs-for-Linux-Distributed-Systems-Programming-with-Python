"""Tests for the completed SigRaft service and release checkpoint."""

from __future__ import annotations

import http.client
import json
from copy import deepcopy
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from lab_39_sigraft_service import __version__
from lab_39_sigraft_service.fabric_executor import (
    FabricExecutorTask,
    VerificationError,
)
from lab_39_sigraft_service.redfish import ManagedSystem, RedfishInventory
from lab_39_sigraft_service.release import (
    ArtifactValidationError,
    ReleaseEvidence,
    load_release_bundle,
    onprem_stage_commands,
    rollback_command,
    stage_scripts,
    validate_onprem_pipeline,
)
from lab_39_sigraft_service.sigraft_service import SigRaftService, run_server
from lab_39_sigraft_service.sigraftctl import SigRaftClient


def test_sigraft_service_and_client_contract_work_end_to_end() -> None:
    hosted = run_server(
        SigRaftService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    try:
        client = SigRaftClient(hosted.base_url)
        metadata = client.metadata()
        submitted = client.submit("ship release", checkpoint=38)
        status = client.status(submitted.task_id)

        assert metadata["service"] == "sigraft"
        assert metadata["release_digest"].startswith("sha256:")
        assert submitted.task_id == "task-1"
        assert submitted.action == "ship release"
        assert submitted.state == "queued"
        assert submitted.checkpoint == 38
        assert status == submitted
    finally:
        hosted.close()


def test_graphql_endpoint_uses_the_same_task_state_as_rest() -> None:
    hosted = run_server(
        SigRaftService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    try:
        client = SigRaftClient(hosted.base_url)
        mutation = client.graphql(
            """
            mutation Submit($action: String!) {
              submitTask(action: $action, checkpoint: 39) {
                id
                action
                state
                checkpoint
              }
            }
            """,
            variables={"action": "schedule workload"},
            scopes=frozenset({"tasks:write"}),
        )
        task_id = mutation["data"]["submitTask"]["id"]
        rest = client.status(task_id)
        query = client.graphql(
            "query($id: ID!) { task(id: $id) { id state action } }",
            variables={"id": task_id},
            scopes=frozenset({"tasks:read"}),
        )

        assert rest.action == "schedule workload"
        assert query["data"]["task"] == {
            "id": task_id,
            "state": "QUEUED",
            "action": "schedule workload",
        }
    finally:
        hosted.close()


def test_graphql_returns_operation_errors_with_http_success() -> None:
    hosted = run_server(
        SigRaftService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    try:
        result = SigRaftClient(hosted.base_url).graphql(
            "{ tasks(first: 101) { id } }",
            scopes=frozenset({"tasks:read"}),
        )
        denied = SigRaftClient(hosted.base_url).graphql("{ tasks { id } }")

        assert result["data"] is None
        assert result["errors"][0]["message"] == "first must be between 1 and 100"
        assert denied["errors"][0]["extensions"]["code"] == "FORBIDDEN"
    finally:
        hosted.close()


def test_graphql_rejects_invalid_http_envelope() -> None:
    hosted = run_server(
        SigRaftService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    try:
        request = Request(
            f"{hosted.base_url}/graphql",
            data=json.dumps({"query": 17}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request)
        assert exc_info.value.code == 400
    finally:
        hosted.close()


def test_resource_scheduler_places_job_and_returns_enforcement_plan() -> None:
    hosted = run_server(
        SigRaftService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    try:
        client = SigRaftClient(hosted.base_url)
        node = client._request(
            "POST",
            "/scheduler/nodes",
            {
                "node_id": "compute-1",
                "cpu_ids": [0, 1, 2, 3],
                "memory_mb": 8192,
                "gpus": [{"device_id": "gpu-0", "device_class": "a100", "numa_node": 0}],
                "numa_cpus": {"0": [0, 1, 2, 3]},
                "labels": ["linux", "gpu"],
                "heartbeat_at": 100,
            },
        )
        job = client._request(
            "POST",
            "/scheduler/jobs",
            {
                "project": "research",
                "action": "train model",
                "priority": 80,
                "max_attempts": 2,
                "resources": {
                    "cpu_cores": 2,
                    "memory_mb": 2048,
                    "gpu_count": 1,
                    "gpu_class": "a100",
                    "numa_node": 0,
                    "labels": ["gpu"],
                },
            },
        )
        scheduled = client._request(
            "POST",
            "/scheduler/run",
            {
                "caller_id": "sigraft-scheduler",
                "now": 100,
                "lease_seconds": 20,
            },
        )
        status = client._request("GET", f"/scheduler/jobs/{job['task_id']}")

        assert node == {"status": "recorded"}
        assert status["state"] == "scheduled"
        assert scheduled["dispatches"][0] == {
            "allocation_id": "allocation-2",
            "task_id": job["task_id"],
            "node_id": "compute-1",
            "queue": "sigraft.node.compute-1",
            "cpu_ids": [0, 1],
            "memory_max_bytes": 2048 * 1024 * 1024,
            "gpu_ids": ["gpu-0"],
            "numa_node": 0,
            "environment": {"CUDA_VISIBLE_DEVICES": "gpu-0"},
        }
    finally:
        hosted.close()


def test_resource_scheduler_rejects_impossible_request() -> None:
    hosted = run_server(
        SigRaftService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    try:
        client = SigRaftClient(hosted.base_url)
        client._request(
            "POST",
            "/scheduler/nodes",
            {
                "node_id": "compute-1",
                "cpu_ids": [0, 1],
                "memory_mb": 1024,
                "heartbeat_at": 100,
            },
        )
        with pytest.raises(HTTPError) as exc_info:
            client._request(
                "POST",
                "/scheduler/jobs",
                {
                    "project": "research",
                    "action": "oversized",
                    "resources": {"cpu_cores": 8, "memory_mb": 8192},
                },
            )
        assert exc_info.value.code == 400
    finally:
        hosted.close()


def test_release_bundle_validates_pipeline_playbook_and_evidence() -> None:
    bundle = load_release_bundle()

    assert bundle.evidence.staging_digest == bundle.evidence.digest
    assert bundle.evidence.production_digest == bundle.evidence.digest
    assert "make structure" in stage_scripts(bundle.pipeline, "Quality")
    assert "make labs" in stage_scripts(bundle.pipeline, "Quality")
    assert "@$(previousDigest)" in rollback_command(bundle.pipeline)
    assert onprem_stage_commands(bundle.onprem_pipeline, "Quality")[:2] == [
        ["make", "structure"],
        ["make", "labs"],
    ]


def test_release_evidence_blocks_missing_quality_or_commit_mismatch() -> None:
    bundle = load_release_bundle()
    missing_quality = ReleaseEvidence.from_mapping(
        {
            "commit": bundle.evidence.commit,
            "qualityGates": {"structure": True, "labs": False, "coverage": True},
            "componentEvidence": bundle.evidence.component_evidence,
            "imageRepository": bundle.evidence.image_repository,
            "digest": bundle.evidence.digest,
            "previousDigest": bundle.evidence.previous_digest,
            "stagingDigest": bundle.evidence.staging_digest,
            "productionDigest": bundle.evidence.production_digest,
        }
    )

    with pytest.raises(ArtifactValidationError, match="quality gate"):
        missing_quality.validate_for_deploy(bundle.evidence.commit)
    with pytest.raises(ArtifactValidationError, match="exact commit"):
        bundle.evidence.validate_for_deploy("0000000000000000000000000000000000000000")


def test_pipeline_uses_same_digest_for_staging_and_production() -> None:
    bundle = load_release_bundle()
    staging = bundle.pipeline["stages"][3]["variables"]
    production_stage = bundle.pipeline["stages"][4]
    production = production_stage["variables"]
    rollback_stage = bundle.pipeline["stages"][5]

    assert staging["sigraftDigest"] == production["sigraftDigest"]
    assert staging["previousDigest"] == production["previousDigest"]
    assert "Build" in production_stage["dependsOn"]
    assert "Build" in rollback_stage["dependsOn"]


def test_pipeline_previews_and_converges_bicep_before_building() -> None:
    bundle = load_release_bundle()
    scripts = stage_scripts(bundle.pipeline, "Infrastructure")

    assert any("az bicep build" in script for script in scripts)
    assert sum("az deployment group what-if" in script for script in scripts) == 2
    assert sum("az deployment group create" in script for script in scripts) == 2
    assert bundle.pipeline["stages"][2]["dependsOn"] == "Infrastructure"


def test_release_evidence_connects_the_earlier_product_checkpoints() -> None:
    evidence = load_release_bundle().evidence

    assert evidence.component_evidence == {
        "projectTemplate": "copier",
        "executableFormats": ["ELF", "PE"],
        "objectModel": [
            "dataclass-slots",
            "repository-protocol",
            "polymorphic-handlers",
        ],
        "algorithmContracts": [
            "red-black-priority-index",
            "dependency-dag",
            "breadth-first-routing",
        ],
        "diagnosticContracts": [
            "monotonic-timing",
            "cprofile",
            "pyperf",
            "linux-command-plans",
        ],
        "concurrencyContracts": [
            "bounded-visibility-queue",
            "spawn-process-workers",
            "manager-coordination",
            "shared-memory-slices",
            "numa-aware-placement",
        ],
        "acceleratorContracts": [
            "pci-sysfs-topology",
            "numa-device-placement",
            "iommu-group-boundary",
            "gpu-nic-transfer-plan",
            "roce-congestion-policy",
        ],
        "languageRuntime": [
            "runtime-host-artifact-boundaries",
            "source-token-ast-code-object",
            "rv32i-add-full-adder-trace",
            "teaching-bytecode-vm",
            "typed-pyparsing-query",
            "cpython-3.14.7-source-pin",
            "relay-task-id-opcode",
        ],
        "streamBuffering": [
            "bounded-ring-buffer",
            "length-prefix",
            "pipe-backpressure",
        ],
        "networkStack": [
            "tcp-byte-stream",
            "scapy-ethernet-ip-tcp-round-trip",
            "top-down-encapsulation",
            "bottom-up-decapsulation",
        ],
        "taskTimeContract": ["UTC", "monotonic-deadline", "vector-clock"],
        "backgroundWorkers": [
            "celery-json-task",
            "valkey-redis-transport",
            "late-acknowledgement",
            "worker-loss-redelivery",
            "bounded-prefetch",
        ],
        "rpcApis": [
            "fastapi-asgi",
            "pydantic-domain-validation",
            "bounded-request-metadata",
            "uvicorn-app-factory",
            "optional-uvloop",
            "event-loop-blocking-test",
        ],
        "distributedCompute": [
            "ray-single-node-cluster",
            "bounded-object-references",
            "resource-label-validation",
            "atomic-inflight-deduplication",
            "fingerprint-conflict",
            "bounded-actor-ledger",
            "usage-stats-opt-out",
        ],
        "realtimeApis": [
            "zeromq-xpub-sub",
            "websocket-replay",
            "graphql-query-mutation",
            "graphql-subscription",
            "graphql-http-endpoint",
        ],
        "resourceScheduling": [
            "typed-resource-request",
            "bounded-node-heartbeat",
            "admission-control",
            "deterministic-placement",
            "raft-backed-reservation",
            "node-specific-dispatch",
            "cgroup-affinity-numa-gpu-plan",
            "allocation-lease-recovery",
            "priority-fifo-quota",
        ],
        "infrastructureAsCode": [
            "bicep-modules",
            "what-if",
            "deployment-stacks",
            "aks-workload-identity",
            "acr",
        ],
        "deploymentAdapters": ["docker-sdk", "kubernetes-python", "openstacksdk"],
        "kubernetesDelivery": [
            "clusterip-service",
            "nginx-ingress",
            "startup-readiness-liveness",
            "hpa",
            "pdb",
            "topology-spread",
            "rolling",
            "canary",
            "blue-green",
            "staging-production",
        ],
        "observability": [
            "opentelemetry-python",
            "otlp",
            "w3c-trace-context",
            "jaeger",
            "prometheus",
            "loki",
            "grafana",
            "azure-monitor",
        ],
        "operationalAnalysis": [
            "numpy",
            "pandas",
            "matplotlib",
            "scipy",
            "statsmodels",
            "descriptive-statistics",
            "confidence-interval",
            "mann-whitney",
            "ols-diagnostics",
            "web-report",
            "managed-refresh",
        ],
        "runtimeConfiguration": [
            "validated-toml",
            "content-revision",
            "monotonic-generation",
            "two-phase-reload",
            "rollback",
            "content-watcher",
            "sighup-event",
            "restart-required-reporting",
        ],
        "onPremCloud": [
            "qemu-kvm",
            "libvirt",
            "openstack",
            "harbor",
            "kubernetes",
            "nginx",
            "python-reconciliation",
        ],
        "dataCenterManagement": [
            "redfish-client",
            "service-root",
            "systems-collection",
            "same-origin-pagination",
            "read-only-inventory-endpoint",
        ],
    }


def test_fabric_executor_fails_when_one_host_reports_the_wrong_digest() -> None:
    good_host = run_server(
        SigRaftService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    bad_host = run_server(
        SigRaftService(
            release_digest="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        )
    )
    try:
        task = FabricExecutorTask.from_http()
        good_target = good_host.base_url.removeprefix("http://")
        bad_target = bad_host.base_url.removeprefix("http://")

        with pytest.raises(VerificationError, match=bad_target):
            task.verify(
                [good_target, bad_target],
                "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )
    finally:
        good_host.close()
        bad_host.close()


def test_rollback_command_references_previous_digest() -> None:
    bundle = load_release_bundle()

    assert "kubectl set image deployment/sigraft" in rollback_command(bundle.pipeline)
    assert "@$(previousDigest)" in rollback_command(bundle.pipeline)


def test_onprem_pipeline_builds_once_promotes_one_digest_and_keeps_rollback() -> None:
    pipeline = load_release_bundle().onprem_pipeline
    build = onprem_stage_commands(pipeline, "BuildOnce")
    staging = " ".join(
        value for command in onprem_stage_commands(pipeline, "DeployStaging") for value in command
    )
    production = " ".join(
        value
        for command in onprem_stage_commands(pipeline, "DeployProduction")
        for value in command
    )
    rollback = " ".join(
        value for command in onprem_stage_commands(pipeline, "Rollback") for value in command
    )

    assert sum(command[:3] == ["docker", "buildx", "build"] for command in build) == 1
    assert "{sigraft_digest}" in staging
    assert "{sigraft_digest}" in production
    assert "{previous_digest}" in rollback


def test_onprem_pipeline_rejects_tag_deployment() -> None:
    pipeline = deepcopy(load_release_bundle().onprem_pipeline)
    deployment = pipeline["stages"][2]["commands"][0]
    deployment[-1] = "sigraft=harbor.cloud.sigraft.test/sigraft/sigraft:latest"

    with pytest.raises(ArtifactValidationError, match="resolved digest"):
        validate_onprem_pipeline(pipeline)


def test_version_is_exposed() -> None:
    assert __version__


def test_read_only_redfish_service_exposes_bounded_system_inventory() -> None:
    hosted = run_server(
        SigRaftService(
            release_digest="sha256:" + "a" * 64,
            redfish=RedfishInventory(
                (
                    ManagedSystem(
                        "gpu-node-1",
                        "GPU node 1",
                        "Rack server",
                        "Example Systems",
                        "EXAMPLE-001",
                        "On",
                        "OK",
                    ),
                )
            ),
        )
    )
    try:
        with urlopen(f"{hosted.base_url}/redfish/v1/") as response:
            root = json.load(response)
        with urlopen(f"{hosted.base_url}/redfish/v1/Systems") as response:
            collection = json.load(response)
        with urlopen(f"{hosted.base_url}/redfish/v1/Systems/gpu-node-1") as response:
            system = json.load(response)

        assert root["Systems"]["@odata.id"] == "/redfish/v1/Systems"
        assert collection["Members@odata.count"] == 1
        assert system["PowerState"] == "On"
        assert system["Status"]["Health"] == "OK"

        with pytest.raises(HTTPError) as blocked:
            urlopen(
                Request(
                    f"{hosted.base_url}/redfish/v1/Systems/gpu-node-1/Actions/Reset",
                    data=b"{}",
                    method="POST",
                )
            )
        assert blocked.value.code == 404
    finally:
        hosted.close()


@pytest.mark.parametrize(
    ("body", "content_length"),
    [
        (b"[]", "2"),
        (json.dumps({"action": "run", "checkpoint": []}).encode(), None),
    ],
)
def test_invalid_json_shapes_return_bad_request(body: bytes, content_length: str | None) -> None:
    hosted = run_server(SigRaftService(release_digest="sha256:" + "a" * 64))
    host, port = hosted.server.server_address[:2]
    connection = http.client.HTTPConnection(str(host), int(port), timeout=2)
    try:
        connection.putrequest("POST", "/tasks")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", content_length or str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
        hosted.close()


@pytest.mark.parametrize("content_length", ["invalid", "-1"])
def test_invalid_content_length_is_rejected_without_blocking(content_length: str) -> None:
    hosted = run_server(SigRaftService(release_digest="sha256:" + "a" * 64))
    host, port = hosted.server.server_address[:2]
    connection = http.client.HTTPConnection(str(host), int(port), timeout=2)
    try:
        connection.putrequest("POST", "/tasks")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", content_length)
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
        hosted.close()
