"""Tests for the relay release pipeline checkpoint."""

from __future__ import annotations

import pytest

from lab_30_release_pipeline import __version__
from lab_30_release_pipeline.fabric_executor import (
    FabricExecutorTask,
    VerificationError,
)
from lab_30_release_pipeline.relay_api import RelayService, run_server
from lab_30_release_pipeline.relayctl import RelayCtlClient
from lab_30_release_pipeline.release import (
    ArtifactValidationError,
    ReleaseEvidence,
    load_release_bundle,
    rollback_command,
    stage_scripts,
)


def test_relay_api_and_relayctl_contract_work_end_to_end() -> None:
    hosted = run_server(
        RelayService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    try:
        client = RelayCtlClient(hosted.base_url)
        metadata = client.metadata()
        submitted = client.submit("ship release", checkpoint=30)
        status = client.status(submitted.task_id)

        assert metadata["service"] == "relay"
        assert metadata["release_digest"].startswith("sha256:")
        assert submitted.task == "ship release"
        assert submitted.checkpoint == 30
        assert status == submitted
    finally:
        hosted.close()


def test_release_bundle_validates_pipeline_playbook_and_evidence() -> None:
    bundle = load_release_bundle()

    assert bundle.evidence.staging_digest == bundle.evidence.digest
    assert bundle.evidence.production_digest == bundle.evidence.digest
    assert "make structure" in stage_scripts(bundle.pipeline, "Quality")
    assert "make labs" in stage_scripts(bundle.pipeline, "Quality")
    assert "@$(previousDigest)" in rollback_command(bundle.pipeline)


def test_release_evidence_blocks_missing_quality_or_commit_mismatch() -> None:
    bundle = load_release_bundle()
    missing_quality = ReleaseEvidence.from_mapping(
        {
            "commit": bundle.evidence.commit,
            "qualityGates": {"structure": True, "labs": False, "coverage": True},
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
    staging = bundle.pipeline["stages"][2]["variables"]
    production = bundle.pipeline["stages"][3]["variables"]

    assert staging["relayDigest"] == production["relayDigest"]
    assert staging["previousDigest"] == production["previousDigest"]


def test_fabric_executor_fails_when_one_host_reports_the_wrong_digest() -> None:
    good_host = run_server(
        RelayService(
            release_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    )
    bad_host = run_server(
        RelayService(
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

    assert "kubectl set image deployment/relay" in rollback_command(bundle.pipeline)
    assert "@$(previousDigest)" in rollback_command(bundle.pipeline)


def test_version_is_exposed() -> None:
    assert __version__
