# SPDX-License-Identifier: Apache-2.0
"""Release plans must retain executable documentation checks and their output."""

from copy import deepcopy

import pytest

from lab_39_sigraft_service.release import (
    ArtifactValidationError,
    load_release_bundle,
    validate_onprem_pipeline,
    validate_pipeline_definition,
)


@pytest.mark.parametrize("kind", ["build", "publish", "suppression"])
def test_azure_docs_step_is_required(kind: str) -> None:
    pipeline = deepcopy(load_release_bundle().pipeline)
    steps = pipeline["stages"][0]["jobs"][0]["steps"]
    if kind == "build":
        steps[:] = [step for step in steps if "sphinx" not in step.get("script", "")]
    elif kind == "publish":
        steps[:] = [step for step in steps if "publish" not in step]
    else:
        for step in steps:
            if "sphinx" in step.get("script", ""):
                step["continueOnError"] = True
    with pytest.raises(ArtifactValidationError, match="documentation"):
        validate_pipeline_definition(pipeline)


def test_onprem_docs_archive_requires_successful_build_first() -> None:
    pipeline = deepcopy(load_release_bundle().onprem_pipeline)
    commands = pipeline["stages"][0]["commands"]
    commands[2], commands[3] = commands[3], commands[2]
    with pytest.raises(ArtifactValidationError, match="documentation"):
        validate_onprem_pipeline(pipeline)
