"""Offline validation for the SigRaft release pipeline artifacts."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_OUTPUT = "$[ stageDependencies.Build.BuildOnce.outputs['captureDigest.sigraftDigest'] ]"
_PREVIOUS_OUTPUT = "$[ stageDependencies.Build.BuildOnce.outputs['captureDigest.previousDigest'] ]"
_ALLOWED_PLAYBOOK_MODULES = {
    "ansible.builtin.package",
    "ansible.builtin.group",
    "ansible.builtin.user",
    "ansible.builtin.file",
    "ansible.builtin.copy",
    "ansible.builtin.systemd_service",
}
_FORBIDDEN_PLAYBOOK_MODULES = {
    "ansible.builtin.shell",
    "ansible.builtin.command",
    "ansible.builtin.raw",
    "ansible.builtin.script",
}
_ONPREM_PROGRAMS = frozenset({"docker", "kubectl", "make", "python", "skopeo"})
_ONPREM_REGISTRY = "harbor.cloud.sigraft.test/sigraft/sigraft"
_ONPREM_DIGEST = "{sigraft_digest}"
_ONPREM_PREVIOUS_DIGEST = "{previous_digest}"


class ArtifactValidationError(ValueError):
    """Raised when a release artifact breaks a required invariant."""


@dataclass(frozen=True)
class ReleaseEvidence:
    """Recorded evidence for one release candidate."""

    commit: str
    quality_gates: dict[str, bool]
    component_evidence: dict[str, object]
    image_repository: str
    digest: str
    previous_digest: str
    staging_digest: str
    production_digest: str

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> ReleaseEvidence:
        """Build release evidence from JSON data."""

        quality = payload.get("qualityGates")
        if not isinstance(quality, dict):
            raise ArtifactValidationError("release evidence must declare quality gates")
        gates = {str(name): bool(value) for name, value in quality.items()}
        components = payload.get("componentEvidence")
        if not isinstance(components, dict):
            raise ArtifactValidationError("release evidence must declare component evidence")
        return cls(
            commit=str(payload.get("commit", "")),
            quality_gates=gates,
            component_evidence={str(name): value for name, value in components.items()},
            image_repository=str(payload.get("imageRepository", "")),
            digest=str(payload.get("digest", "")),
            previous_digest=str(payload.get("previousDigest", "")),
            staging_digest=str(payload.get("stagingDigest", "")),
            production_digest=str(payload.get("productionDigest", "")),
        )

    @classmethod
    def from_path(cls, path: Path) -> ReleaseEvidence:
        """Load release evidence from *path*."""

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ArtifactValidationError("release evidence file must contain a JSON object")
        return cls.from_mapping(cast(dict[str, Any], payload))

    def validate_for_deploy(self, expected_commit: str) -> None:
        """Verify the evidence is good enough to deploy *expected_commit*."""

        if self.commit != expected_commit:
            raise ArtifactValidationError(
                "release evidence must match the exact commit being deployed"
            )
        if not self.quality_gates or not all(self.quality_gates.values()):
            raise ArtifactValidationError("release evidence must show every quality gate passing")
        if not self.image_repository:
            raise ArtifactValidationError("release evidence must declare the image repository")
        required_components: dict[str, object] = {
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
        if self.component_evidence != required_components:
            raise ArtifactValidationError(
                "release evidence must carry every required SigRaft component contract"
            )
        for digest in (
            self.digest,
            self.previous_digest,
            self.staging_digest,
            self.production_digest,
        ):
            if _DIGEST_PATTERN.match(digest) is None:
                raise ArtifactValidationError("release evidence must use sha256 digests")
        if self.digest == self.previous_digest:
            raise ArtifactValidationError(
                "release evidence must keep the previous digest for rollback"
            )
        if self.staging_digest != self.digest or self.production_digest != self.digest:
            raise ArtifactValidationError(
                "staging and production must promote the same built digest"
            )


@dataclass(frozen=True)
class ReleaseBundle:
    """All release artifacts used by the Chapter 39 lab."""

    pipeline: dict[str, Any]
    onprem_pipeline: dict[str, Any]
    playbook: list[dict[str, Any]]
    evidence: ReleaseEvidence


def lab_root() -> Path:
    """Return the lab root directory."""

    return Path(__file__).resolve().parents[2]


def load_release_bundle(root: Path | None = None) -> ReleaseBundle:
    """Load and validate the release pipeline artifacts."""

    actual_root = root if root is not None else lab_root()
    pipeline_path = actual_root / "artifacts/azure-pipelines.yml"
    onprem_pipeline_path = actual_root / "artifacts/onprem-release.yml"
    playbook_path = actual_root / "artifacts/self_hosted_agents.yml"
    evidence_path = actual_root / "artifacts/release-evidence.json"

    pipeline = _expect_mapping(
        yaml.safe_load(pipeline_path.read_text(encoding="utf-8")), "pipeline"
    )
    onprem_pipeline = _expect_mapping(
        yaml.safe_load(onprem_pipeline_path.read_text(encoding="utf-8")),
        "on-prem pipeline",
    )
    playbook = yaml.safe_load(playbook_path.read_text(encoding="utf-8"))
    if not isinstance(playbook, list):
        raise ArtifactValidationError("self-hosted agent playbook must be a YAML list")
    typed_playbook = [_expect_mapping(item, "playbook entry") for item in playbook]
    evidence = ReleaseEvidence.from_path(evidence_path)
    evidence.validate_for_deploy(evidence.commit)
    validate_pipeline_definition(pipeline)
    validate_onprem_pipeline(onprem_pipeline)
    validate_playbook(typed_playbook)
    return ReleaseBundle(
        pipeline=pipeline,
        onprem_pipeline=onprem_pipeline,
        playbook=typed_playbook,
        evidence=evidence,
    )


def onprem_stage_commands(pipeline: dict[str, Any], stage_name: str) -> list[list[str]]:
    """Return typed argument vectors for one on-prem release stage."""

    stage = _onprem_stage(pipeline, stage_name)
    commands = _expect_list(stage.get("commands"), f"{stage_name} commands")
    typed: list[list[str]] = []
    for command in commands:
        values = _expect_list(command, f"{stage_name} command")
        if not values or not all(isinstance(value, str) for value in values):
            raise ArtifactValidationError(
                f"{stage_name} commands must be non-empty string argument arrays"
            )
        typed.append(cast(list[str], values))
    return typed


def stage_scripts(pipeline: dict[str, Any], stage_name: str) -> list[str]:
    """Return every script step for the named stage."""

    stage = _stage(pipeline, stage_name)
    jobs = _expect_list(stage.get("jobs"), f"{stage_name} jobs")
    scripts: list[str] = []
    for job in jobs:
        job_mapping = _expect_mapping(job, f"{stage_name} job")
        if "steps" in job_mapping:
            steps = _expect_list(job_mapping.get("steps"), f"{stage_name} steps")
        else:
            strategy = _expect_mapping(job_mapping.get("strategy"), f"{stage_name} strategy")
            run_once = _expect_mapping(strategy.get("runOnce"), f"{stage_name} runOnce")
            deploy = _expect_mapping(run_once.get("deploy"), f"{stage_name} deploy")
            steps = _expect_list(deploy.get("steps"), f"{stage_name} deploy steps")
        for step in steps:
            step_mapping = _expect_mapping(step, f"{stage_name} step")
            script = step_mapping.get("script")
            if isinstance(script, str):
                scripts.append(script)
    return scripts


def rollback_command(pipeline: dict[str, Any]) -> str:
    """Return the rollback command from the pipeline."""

    scripts = stage_scripts(pipeline, "Rollback")
    for script in scripts:
        if "kubectl set image deployment/sigraft" in script:
            return script
    raise ArtifactValidationError("rollback stage must define a kubectl rollback command")


def validate_pipeline_definition(pipeline: dict[str, Any]) -> None:
    """Check the Azure Pipelines YAML semantics offline."""

    stages = _expect_list(pipeline.get("stages"), "stages")
    stage_names = [_expect_mapping(stage, "stage").get("stage") for stage in stages]
    for expected in (
        "Quality",
        "Infrastructure",
        "Build",
        "DeployStaging",
        "DeployProduction",
        "Rollback",
    ):
        if expected not in stage_names:
            raise ArtifactValidationError(f"pipeline is missing the {expected} stage")

    quality_scripts = stage_scripts(pipeline, "Quality")
    if "make structure" not in quality_scripts or "make labs" not in quality_scripts:
        raise ArtifactValidationError("quality stage must gate on make structure and make labs")
    if not any(
        "verify-evidence" in script and "$(Build.SourceVersion)" in script
        for script in quality_scripts
    ):
        raise ArtifactValidationError("quality stage must verify evidence for the exact commit")

    infrastructure = _stage(pipeline, "Infrastructure")
    if _stage_dependencies(infrastructure, "Infrastructure") != ["Quality"]:
        raise ArtifactValidationError("infrastructure must follow the quality stage")
    infrastructure_scripts = stage_scripts(pipeline, "Infrastructure")
    if not any("az bicep build" in script for script in infrastructure_scripts):
        raise ArtifactValidationError("infrastructure must compile the Bicep source")
    if sum("az deployment group what-if" in script for script in infrastructure_scripts) != 2:
        raise ArtifactValidationError("infrastructure must preview staging and production")
    if sum("az deployment group create" in script for script in infrastructure_scripts) != 2:
        raise ArtifactValidationError("infrastructure must converge staging and production")

    build_stage = _stage(pipeline, "Build")
    if "Infrastructure" not in _stage_dependencies(build_stage, "Build"):
        raise ArtifactValidationError("image build must follow infrastructure convergence")
    build_scripts = stage_scripts(pipeline, "Build")
    build_count = sum("az acr build" in script for script in build_scripts)
    if build_count != 1:
        raise ArtifactValidationError("pipeline must build the image exactly once")
    if not any("emit-digests" in script for script in build_scripts):
        raise ArtifactValidationError("build stage must capture the built digest as an output")

    deploy_staging = _stage(pipeline, "DeployStaging")
    deploy_production = _stage(pipeline, "DeployProduction")
    rollback_stage = _stage(pipeline, "Rollback")
    if "Build" not in _stage_dependencies(deploy_staging, "DeployStaging"):
        raise ArtifactValidationError("staging deploy must depend on the build stage")
    production_dependencies = _stage_dependencies(deploy_production, "DeployProduction")
    if "DeployStaging" not in production_dependencies:
        raise ArtifactValidationError("production deploy must depend on staging")
    if "Build" not in production_dependencies:
        raise ArtifactValidationError("production deploy must directly depend on the build stage")
    rollback_dependencies = _stage_dependencies(rollback_stage, "Rollback")
    if "DeployProduction" not in rollback_dependencies:
        raise ArtifactValidationError("rollback must follow production")
    if "Build" not in rollback_dependencies:
        raise ArtifactValidationError("rollback must directly depend on the build stage")
    if rollback_stage.get("condition") != "failed('DeployProduction')":
        raise ArtifactValidationError("rollback must only run when production fails")

    for stage_name in ("DeployStaging", "DeployProduction"):
        stage = _stage(pipeline, stage_name)
        variables = _expect_mapping(stage.get("variables"), f"{stage_name} variables")
        if variables.get("sigraftDigest") != _DIGEST_OUTPUT:
            raise ArtifactValidationError(f"{stage_name} must promote the build digest output")
        if variables.get("previousDigest") != _PREVIOUS_OUTPUT:
            raise ArtifactValidationError(f"{stage_name} must keep the previous digest output")
        scripts = stage_scripts(pipeline, stage_name)
        if not any("@$(sigraftDigest)" in script for script in scripts):
            raise ArtifactValidationError(f"{stage_name} must deploy the promoted digest")
        if not any(
            "fabric_executor verify" in script and "--expected-digest $(sigraftDigest)" in script
            for script in scripts
        ):
            raise ArtifactValidationError(
                f"{stage_name} must verify each host against the promoted digest"
            )

    staging_scripts = stage_scripts(pipeline, "DeployStaging")
    if not any(
        "observation_probe" in script
        and "--baseline-url $(baselineUrl)" in script
        and "--candidate-url $(candidateUrl)" in script
        and "--output $(stagingObservationsFile)" in script
        for script in staging_scripts
    ):
        raise ArtifactValidationError("staging must probe both public release endpoints")
    if not any(
        "analytics_service verify-release" in script
        and "--candidate-digest $(sigraftDigest)" in script
        and "--observations $(stagingObservationsFile)" in script
        for script in staging_scripts
    ):
        raise ArtifactValidationError("staging must pass the operational analysis gate")

    rollback_variables = _expect_mapping(rollback_stage.get("variables"), "rollback variables")
    if rollback_variables.get("previousDigest") != _PREVIOUS_OUTPUT:
        raise ArtifactValidationError("rollback stage must use the previous digest output")
    if "@$(previousDigest)" not in rollback_command(pipeline):
        raise ArtifactValidationError("rollback command must reference the previous digest")


def validate_onprem_pipeline(pipeline: dict[str, Any]) -> None:
    """Check the pipeline-neutral release plan for the reader-managed cloud."""

    if pipeline.get("version") != 1:
        raise ArtifactValidationError("on-prem pipeline version must be 1")
    if pipeline.get("registry") != _ONPREM_REGISTRY:
        raise ArtifactValidationError("on-prem pipeline must publish to the SigRaft Harbor project")
    variables = _expect_mapping(pipeline.get("variables"), "on-prem variables")
    if variables != {
        "commit": "{commit}",
        "digest": _ONPREM_DIGEST,
        "previous_digest": _ONPREM_PREVIOUS_DIGEST,
        "baseline_url": "{baseline_url}",
        "candidate_url": "{candidate_url}",
        "staging_observations": "{staging_observations}",
    }:
        raise ArtifactValidationError("on-prem pipeline variables must remain symbolic")

    required = (
        "Quality",
        "BuildOnce",
        "DeployStaging",
        "AnalyseStaging",
        "ProductionApproval",
        "DeployProduction",
        "Rollback",
    )
    stages = _expect_list(pipeline.get("stages"), "on-prem stages")
    names = [_expect_mapping(stage, "on-prem stage").get("stage") for stage in stages]
    if tuple(names) != required:
        raise ArtifactValidationError("on-prem pipeline stages must retain their safe order")

    expected_dependencies = {
        "BuildOnce": ["Quality"],
        "DeployStaging": ["BuildOnce"],
        "AnalyseStaging": ["DeployStaging"],
        "ProductionApproval": ["AnalyseStaging"],
        "DeployProduction": ["ProductionApproval"],
        "Rollback": ["DeployProduction"],
    }
    for stage_name, dependencies in expected_dependencies.items():
        stage = _onprem_stage(pipeline, stage_name)
        if _stage_dependencies(stage, stage_name) != dependencies:
            raise ArtifactValidationError(f"on-prem {stage_name} must depend on {dependencies[-1]}")

    commands = [
        command
        for stage_name in required
        for command in onprem_stage_commands(pipeline, stage_name)
    ]
    if any(command[0] not in _ONPREM_PROGRAMS for command in commands):
        raise ArtifactValidationError("on-prem pipeline contains an unapproved executable")
    build_commands = onprem_stage_commands(pipeline, "BuildOnce")
    if sum(command[:3] == ["docker", "buildx", "build"] for command in build_commands) != 1:
        raise ArtifactValidationError("on-prem pipeline must build exactly one OCI artifact")
    if not any(
        command[:2] == ["skopeo", "copy"]
        and command[-1] == f"docker://{_ONPREM_REGISTRY}:{{commit}}"
        for command in build_commands
    ):
        raise ArtifactValidationError("on-prem pipeline must copy the built artifact to Harbor")
    if not any(
        command[:2] == ["skopeo", "inspect"] and "{{.Digest}}" in command
        for command in build_commands
    ):
        raise ArtifactValidationError("on-prem pipeline must resolve the Harbor digest")

    for stage_name in ("DeployStaging", "DeployProduction"):
        joined = [" ".join(command) for command in onprem_stage_commands(pipeline, stage_name)]
        if not any(f"{_ONPREM_REGISTRY}@{_ONPREM_DIGEST}" in command for command in joined):
            raise ArtifactValidationError(f"on-prem {stage_name} must deploy the resolved digest")
        if not any(
            "fabric_executor verify" in command and f"--expected-digest {_ONPREM_DIGEST}" in command
            for command in joined
        ):
            raise ArtifactValidationError(f"on-prem {stage_name} must verify the running digest")

    analysis = [" ".join(command) for command in onprem_stage_commands(pipeline, "AnalyseStaging")]
    if not any(
        "observation_probe" in command
        and "--baseline-url {baseline_url}" in command
        and "--candidate-url {candidate_url}" in command
        and "--output {staging_observations}" in command
        for command in analysis
    ):
        raise ArtifactValidationError("on-prem staging must probe both public release endpoints")
    if not any(
        "analytics_service verify-release" in command
        and f"--candidate-digest {_ONPREM_DIGEST}" in command
        and "--observations {staging_observations}" in command
        for command in analysis
    ):
        raise ArtifactValidationError("on-prem staging must pass the operational analysis gate")
    approval = _onprem_stage(pipeline, "ProductionApproval")
    if approval.get("manual") is not True or onprem_stage_commands(pipeline, "ProductionApproval"):
        raise ArtifactValidationError("on-prem production requires an external manual approval")
    rollback = _onprem_stage(pipeline, "Rollback")
    rollback_commands = [
        " ".join(command) for command in onprem_stage_commands(pipeline, "Rollback")
    ]
    if rollback.get("onFailure") is not True or not any(
        f"{_ONPREM_REGISTRY}@{_ONPREM_PREVIOUS_DIGEST}" in command for command in rollback_commands
    ):
        raise ArtifactValidationError("on-prem rollback must restore the previous digest")


def validate_playbook(playbook: list[dict[str, Any]]) -> None:
    """Check that the self-hosted agent playbook stays idempotent."""

    if len(playbook) != 1:
        raise ArtifactValidationError("playbook must contain exactly one play")
    play = playbook[0]
    if play.get("hosts") != "sigraft_agents":
        raise ArtifactValidationError("playbook must target the sigraft_agents inventory group")
    tasks = _expect_list(play.get("tasks"), "playbook tasks")
    handlers = _expect_list(play.get("handlers"), "playbook handlers")
    for task in tasks + handlers:
        task_mapping = _expect_mapping(task, "playbook task")
        module_name = _task_module(task_mapping)
        if module_name in _FORBIDDEN_PLAYBOOK_MODULES:
            raise ArtifactValidationError(
                "playbook must stay idempotent and avoid shell-style tasks"
            )
        if module_name not in _ALLOWED_PLAYBOOK_MODULES:
            raise ArtifactValidationError(
                f"playbook module is not approved for offline checks: {module_name}"
            )
        module_args = _expect_mapping(task_mapping.get(module_name), f"{module_name} args")
        if module_name == "ansible.builtin.package" and module_args.get("state") != "present":
            raise ArtifactValidationError("package tasks must declare state: present")
        if module_name == "ansible.builtin.file" and module_args.get("state") != "directory":
            raise ArtifactValidationError("file tasks must converge directories explicitly")
        if module_name == "ansible.builtin.systemd_service" and module_args.get("state") not in {
            "started",
            "restarted",
        }:
            raise ArtifactValidationError("systemd tasks must declare a concrete state")


def _stage(pipeline: dict[str, Any], stage_name: str) -> dict[str, Any]:
    for stage in _expect_list(pipeline.get("stages"), "stages"):
        stage_mapping = _expect_mapping(stage, "stage")
        if stage_mapping.get("stage") == stage_name:
            return stage_mapping
    raise ArtifactValidationError(f"stage {stage_name} was not found")


def _onprem_stage(pipeline: dict[str, Any], stage_name: str) -> dict[str, Any]:
    for stage in _expect_list(pipeline.get("stages"), "on-prem stages"):
        stage_mapping = _expect_mapping(stage, "on-prem stage")
        if stage_mapping.get("stage") == stage_name:
            return stage_mapping
    raise ArtifactValidationError(f"on-prem stage {stage_name} was not found")


def _task_module(task: dict[str, Any]) -> str:
    for key in task:
        if key.startswith("ansible.builtin."):
            return key
    raise ArtifactValidationError("playbook task must use an explicit ansible.builtin module")


def _expect_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{label} must be a mapping")
    return cast(dict[str, Any], value)


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ArtifactValidationError(f"{label} must be a list")
    return value


def _stage_dependencies(stage: dict[str, Any], label: str) -> list[str]:
    dependencies = stage.get("dependsOn")
    if isinstance(dependencies, str):
        return [dependencies]
    if isinstance(dependencies, list) and all(
        isinstance(dependency, str) for dependency in dependencies
    ):
        return dependencies
    raise ArtifactValidationError(f"{label} dependsOn must name one or more stages")


def _emit_digests(evidence: ReleaseEvidence) -> None:
    print(f"##vso[task.setvariable variable=sigraftDigest;isOutput=true]{evidence.digest}")
    print(
        f"##vso[task.setvariable variable=previousDigest;isOutput=true]{evidence.previous_digest}"
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Validate SigRaft release artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-evidence")
    verify.add_argument("--evidence", required=True)
    verify.add_argument("--commit", required=True)

    emit = subparsers.add_parser("emit-digests")
    emit.add_argument("--evidence", required=True)
    emit.add_argument("--commit", required=True)

    args = parser.parse_args(argv)
    evidence = ReleaseEvidence.from_path(Path(args.evidence))
    evidence.validate_for_deploy(args.commit)
    if args.command == "emit-digests":
        _emit_digests(evidence)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
