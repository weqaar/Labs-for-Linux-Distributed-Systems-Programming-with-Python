"""Pinned CPython source and patch contracts for the optional source build."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CPYTHON_TAG = "v3.14.7"
CPYTHON_COMMIT = "823f0323ee6ec1402088b73bce1a38473cac36dc"


@dataclass(frozen=True, slots=True)
class CPythonSource:
    tag: str = CPYTHON_TAG
    commit: str = CPYTHON_COMMIT
    repository: str = "https://github.com/python/cpython.git"

    def clone_command(self, destination: Path) -> tuple[str, ...]:
        return (
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            self.tag,
            self.repository,
            str(destination),
        )

    def verify_command(self, destination: Path) -> tuple[str, ...]:
        return ("git", "-C", str(destination), "rev-parse", "HEAD")


@dataclass(frozen=True, slots=True)
class BuildStep:
    argv: tuple[str, ...]
    purpose: str


def build_plan(checkout: Path, jobs: int) -> tuple[BuildStep, ...]:
    """Return reviewable source-build steps without executing them."""

    if jobs < 1:
        raise ValueError("jobs must be positive")
    return (
        BuildStep(("./configure", "--with-pydebug"), "configure a debug interpreter"),
        BuildStep(("make", f"-j{jobs}"), "generate and build CPython"),
        BuildStep(
            (
                str(checkout / "python"),
                "-m",
                "test",
                "-v",
                "test_relay_opcode",
            ),
            "run the custom opcode regression tests",
        ),
        BuildStep(
            (
                str(checkout / "python"),
                "-m",
                "test",
                "-j4",
                "test_builtin",
                "test_compile",
                "test_dis",
                "test_importlib.test_util",
            ),
            "run the affected CPython regression tests",
        ),
    )


def verify_patch_contract(patch: str) -> tuple[str, ...]:
    """Require the educational patch to cover declaration, execution, and tests."""

    required = (
        "Python/bytecodes.c",
        "Python/codegen.c",
        "Python/bltinmodule.c",
        "Lib/test/test_relay_opcode.py",
        "RELAY_TASK_ID",
        "relay_task_id",
        "PyUnicode_FromFormat",
        "ADDOP",
        "PYC_MAGIC_NUMBER 3628",
    )
    missing = tuple(marker for marker in required if marker not in patch)
    if missing:
        raise ValueError(f"CPython patch is missing required markers: {missing}")
    return required
