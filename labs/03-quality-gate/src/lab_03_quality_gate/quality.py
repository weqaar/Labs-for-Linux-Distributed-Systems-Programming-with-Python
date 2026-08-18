"""Checkpoint 03 for relay: honest JUnit gate evidence for `/tasks`."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

AZURE_PIPELINES_SNIPPET = "\n".join(
    [
        "- script: pybootstrap check --junit-dir $(Build.ArtifactStagingDirectory)/quality",
        "  displayName: Relay quality gates",
        "  continueOnError: true",
        "",
        "- task: PublishTestResults@2",
        "  inputs:",
        "    testResultsFormat: JUnit",
        "    testResultsFiles: '$(Build.ArtifactStagingDirectory)/quality/*.xml'",
        "    failTaskOnFailedTests: true",
        "  displayName: Publish relay gate evidence",
    ]
)


class GateOutcome(str, Enum):
    """The three outcomes a relay quality gate can report."""

    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"


@dataclass(frozen=True)
class GateCommand:
    """A single gate command for relay quality evidence."""

    name: str
    argv: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class ProcessResult:
    """The output from a tool process."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class GateResult:
    """The interpreted outcome of a gate command."""

    name: str
    command: tuple[str, ...]
    outcome: GateOutcome
    summary: str
    detail: str
    exit_code: int | None


class CommandRunner(Protocol):
    """Protocol boundary between quality logic and process execution."""

    def run(self, argv: tuple[str, ...]) -> ProcessResult:
        """Run *argv* and capture its exit code and output."""
        ...


@dataclass(frozen=True)
class GateReport:
    """A relay gate run ready for JUnit export and CI verdicts."""

    suite_name: str
    results: tuple[GateResult, ...]

    @property
    def failures(self) -> int:
        return sum(result.outcome is GateOutcome.FAILED for result in self.results)

    @property
    def errors(self) -> int:
        return sum(result.outcome is GateOutcome.ERRORED for result in self.results)

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        if self.failures:
            return 1
        return 0

    def to_junit_xml(self) -> str:
        suite = ET.Element(
            "testsuite",
            {
                "name": self.suite_name,
                "tests": str(len(self.results)),
                "failures": str(self.failures),
                "errors": str(self.errors),
            },
        )
        for result in self.results:
            case = ET.SubElement(
                suite,
                "testcase",
                {"classname": self.suite_name, "name": result.name},
            )
            if result.outcome is GateOutcome.FAILED:
                failure = ET.SubElement(case, "failure", {"message": result.summary})
                failure.text = result.detail
            elif result.outcome is GateOutcome.ERRORED:
                error = ET.SubElement(case, "error", {"message": result.summary})
                error.text = result.detail
            elif result.detail:
                system_out = ET.SubElement(case, "system-out")
                system_out.text = result.detail
        return ET.tostring(suite, encoding="unicode")


@dataclass(frozen=True)
class GateSuite:
    """A set of relay gates executed under one JUnit suite name."""

    suite_name: str
    commands: tuple[GateCommand, ...]

    def run(self, runner: CommandRunner) -> GateReport:
        return GateReport(
            suite_name=self.suite_name,
            results=tuple(run_gate(command, runner) for command in self.commands),
        )


class SubprocessRunner:
    """Run gate commands through `subprocess.run`."""

    def run(self, argv: tuple[str, ...]) -> ProcessResult:
        completed = subprocess.run(argv, capture_output=True, check=False, text=True)
        return ProcessResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def relay_quality_suite() -> GateSuite:
    """Return the default relay gate suite for the product checkpoints."""

    return GateSuite(
        suite_name="relay-quality",
        commands=(
            GateCommand(
                name="format",
                argv=("ruff", "format", "--check", "src", "tests"),
                description="Check relay source formatting",
            ),
            GateCommand(
                name="lint",
                argv=("ruff", "check", "src", "tests"),
                description="Check relay source for known mistakes",
            ),
            GateCommand(
                name="types",
                argv=("pyright", "src", "tests"),
                description="Check relay type annotations",
            ),
            GateCommand(
                name="test",
                argv=("pytest",),
                description="Run the relay test suite",
            ),
        ),
    )


def run_gate(command: GateCommand, runner: CommandRunner) -> GateResult:
    """Execute one gate and map shell semantics to pass, fail or error."""

    try:
        result = runner.run(command.argv)
    except FileNotFoundError as error:
        tool_name = error.filename or command.argv[0]
        return GateResult(
            name=command.name,
            command=command.argv,
            outcome=GateOutcome.ERRORED,
            summary=f"{command.name} could not start because {tool_name} is missing",
            detail=str(error),
            exit_code=None,
        )

    detail = _detail_from_process(result)
    if result.exit_code == 0:
        return GateResult(
            name=command.name,
            command=command.argv,
            outcome=GateOutcome.PASSED,
            summary=f"{command.name} passed",
            detail=detail,
            exit_code=0,
        )
    if result.exit_code == 1:
        return GateResult(
            name=command.name,
            command=command.argv,
            outcome=GateOutcome.FAILED,
            summary=f"{command.name} found problems",
            detail=detail,
            exit_code=1,
        )
    if result.exit_code == 2:
        summary = f"{command.name} rejected the invocation"
    else:
        summary = f"{command.name} could not run cleanly"
    return GateResult(
        name=command.name,
        command=command.argv,
        outcome=GateOutcome.ERRORED,
        summary=summary,
        detail=detail,
        exit_code=result.exit_code,
    )


def _detail_from_process(result: ProcessResult) -> str:
    parts = [part.strip() for part in (result.stdout, result.stderr) if part.strip()]
    return "\n".join(parts)
