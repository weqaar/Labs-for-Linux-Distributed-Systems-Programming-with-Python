"""Offline validation for the relay release pipeline artifacts."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_OUTPUT = "$[ stageDependencies.Build.BuildOnce.outputs['captureDigest.relayDigest'] ]"
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


class ArtifactValidationError(ValueError):
    """Raised when a release artifact breaks a required invariant."""


@dataclass(frozen=True)
class ReleaseEvidence:
    """Recorded evidence for one release candidate."""

    commit: str
    quality_gates: dict[str, bool]
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
        return cls(
            commit=str(payload.get("commit", "")),
            quality_gates=gates,
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
    """All release artifacts used by the Chapter 30 checkpoint."""

    pipeline: dict[str, Any]
    playbook: list[dict[str, Any]]
    evidence: ReleaseEvidence


def lab_root() -> Path:
    """Return the lab root directory."""

    return Path(__file__).resolve().parents[2]


def load_release_bundle(root: Path | None = None) -> ReleaseBundle:
    """Load and validate the release pipeline artifacts."""

    actual_root = root if root is not None else lab_root()
    pipeline_path = actual_root / "artifacts/azure-pipelines.yml"
    playbook_path = actual_root / "artifacts/self_hosted_agents.yml"
    evidence_path = actual_root / "artifacts/release-evidence.json"

    pipeline = _expect_mapping(
        yaml.safe_load(pipeline_path.read_text(encoding="utf-8")), "pipeline"
    )
    playbook = yaml.safe_load(playbook_path.read_text(encoding="utf-8"))
    if not isinstance(playbook, list):
        raise ArtifactValidationError("self-hosted agent playbook must be a YAML list")
    typed_playbook = [_expect_mapping(item, "playbook entry") for item in playbook]
    evidence = ReleaseEvidence.from_path(evidence_path)
    evidence.validate_for_deploy(evidence.commit)
    validate_pipeline_definition(pipeline)
    validate_playbook(typed_playbook)
    return ReleaseBundle(pipeline=pipeline, playbook=typed_playbook, evidence=evidence)


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
        if "kubectl set image deployment/relay" in script:
            return script
    raise ArtifactValidationError("rollback stage must define a kubectl rollback command")


def validate_pipeline_definition(pipeline: dict[str, Any]) -> None:
    """Check the Azure Pipelines YAML semantics offline."""

    stages = _expect_list(pipeline.get("stages"), "stages")
    stage_names = [_expect_mapping(stage, "stage").get("stage") for stage in stages]
    for expected in ("Quality", "Build", "DeployStaging", "DeployProduction", "Rollback"):
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

    build_scripts = stage_scripts(pipeline, "Build")
    build_count = sum("az acr build" in script for script in build_scripts)
    if build_count != 1:
        raise ArtifactValidationError("pipeline must build the image exactly once")
    if not any("emit-digests" in script for script in build_scripts):
        raise ArtifactValidationError("build stage must capture the built digest as an output")

    deploy_staging = _stage(pipeline, "DeployStaging")
    deploy_production = _stage(pipeline, "DeployProduction")
    rollback_stage = _stage(pipeline, "Rollback")
    if deploy_staging.get("dependsOn") != "Build":
        raise ArtifactValidationError("staging deploy must depend on the build stage")
    if deploy_production.get("dependsOn") != "DeployStaging":
        raise ArtifactValidationError("production deploy must depend on staging")
    if rollback_stage.get("dependsOn") != "DeployProduction":
        raise ArtifactValidationError("rollback must follow production")
    if rollback_stage.get("condition") != "failed('DeployProduction')":
        raise ArtifactValidationError("rollback must only run when production fails")

    for stage_name in ("DeployStaging", "DeployProduction"):
        stage = _stage(pipeline, stage_name)
        variables = _expect_mapping(stage.get("variables"), f"{stage_name} variables")
        if variables.get("relayDigest") != _DIGEST_OUTPUT:
            raise ArtifactValidationError(f"{stage_name} must promote the build digest output")
        if variables.get("previousDigest") != _PREVIOUS_OUTPUT:
            raise ArtifactValidationError(f"{stage_name} must keep the previous digest output")
        scripts = stage_scripts(pipeline, stage_name)
        if not any("@$(relayDigest)" in script for script in scripts):
            raise ArtifactValidationError(f"{stage_name} must deploy the promoted digest")
        if not any(
            "fabric_executor verify" in script and "--expected-digest $(relayDigest)" in script
            for script in scripts
        ):
            raise ArtifactValidationError(
                f"{stage_name} must verify each host against the promoted digest"
            )

    rollback_variables = _expect_mapping(rollback_stage.get("variables"), "rollback variables")
    if rollback_variables.get("previousDigest") != _PREVIOUS_OUTPUT:
        raise ArtifactValidationError("rollback stage must use the previous digest output")
    if "@$(previousDigest)" not in rollback_command(pipeline):
        raise ArtifactValidationError("rollback command must reference the previous digest")


def validate_playbook(playbook: list[dict[str, Any]]) -> None:
    """Check that the self-hosted agent playbook stays idempotent."""

    if len(playbook) != 1:
        raise ArtifactValidationError("playbook must contain exactly one play")
    play = playbook[0]
    if play.get("hosts") != "relay_agents":
        raise ArtifactValidationError("playbook must target the relay_agents inventory group")
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


def _emit_digests(evidence: ReleaseEvidence) -> None:
    print(f"##vso[task.setvariable variable=relayDigest;isOutput=true]{evidence.digest}")
    print(
        f"##vso[task.setvariable variable=previousDigest;isOutput=true]{evidence.previous_digest}"
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Validate relay release artifacts")
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
