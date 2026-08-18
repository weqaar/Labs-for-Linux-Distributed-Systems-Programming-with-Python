"""Tests for the relay quality-gate checkpoint."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import create_autospec, patch

from lab_03_quality_gate import (
    AZURE_PIPELINES_SNIPPET,
    GateCommand,
    GateOutcome,
    GateReport,
    GateResult,
    GateSuite,
    ProcessResult,
    SubprocessRunner,
    __version__,
    run_gate,
)


class RunnerSpec:
    def run(self, argv: tuple[str, ...]) -> ProcessResult:
        raise NotImplementedError


def test_missing_tool_is_reported_as_an_error() -> None:
    runner = create_autospec(RunnerSpec, instance=True, spec_set=True)
    runner.run.side_effect = FileNotFoundError(2, "No such file or directory", "pyright")

    result = run_gate(
        GateCommand("types", ("pyright", "src", "tests"), "Check relay types"),
        runner,
    )

    assert result.outcome is GateOutcome.ERRORED
    assert result.exit_code is None
    assert "pyright is missing" in result.summary


def test_failing_check_is_reported_as_a_failure() -> None:
    runner = create_autospec(RunnerSpec, instance=True, spec_set=True)
    runner.run.return_value = ProcessResult(exit_code=1, stderr="1 failing relay test")

    result = run_gate(
        GateCommand("test", ("pytest",), "Run relay tests"),
        runner,
    )

    assert result.outcome is GateOutcome.FAILED
    assert result.exit_code == 1
    assert result.detail == "1 failing relay test"


def test_bad_invocation_semantics_are_reported_as_an_error() -> None:
    runner = create_autospec(RunnerSpec, instance=True, spec_set=True)
    runner.run.return_value = ProcessResult(
        exit_code=2,
        stderr="usage: ruff format [--check] [FILES]...",
    )

    result = run_gate(
        GateCommand(
            "format",
            ("ruff", "format", "--output-format=json", "src"),
            "Check relay formatting",
        ),
        runner,
    )

    assert result.outcome is GateOutcome.ERRORED
    assert result.exit_code == 2
    assert result.summary == "format rejected the invocation"


def test_suite_exit_code_prefers_error_over_failure() -> None:
    runner = create_autospec(RunnerSpec, instance=True, spec_set=True)
    runner.run.side_effect = [
        ProcessResult(exit_code=1, stderr="lint failed"),
        ProcessResult(exit_code=2, stderr="usage: pyright"),
        ProcessResult(exit_code=0, stdout="tests passed"),
    ]
    suite = GateSuite(
        suite_name="relay-quality",
        commands=(
            GateCommand("lint", ("ruff", "check", "src"), "Lint relay"),
            GateCommand("types", ("pyright", "--bad-flag"), "Typecheck relay"),
            GateCommand("test", ("pytest",), "Test relay"),
        ),
    )

    report = suite.run(runner)

    assert report.exit_code == 2
    assert [result.outcome for result in report.results] == [
        GateOutcome.FAILED,
        GateOutcome.ERRORED,
        GateOutcome.PASSED,
    ]


def test_subprocess_runner_captures_process_output() -> None:
    completed = subprocess.CompletedProcess(
        args=("pytest",),
        returncode=1,
        stdout="relay stdout",
        stderr="relay stderr",
    )

    with patch("lab_03_quality_gate.quality.subprocess.run", return_value=completed):
        result = SubprocessRunner().run(("pytest",))

    assert result == ProcessResult(exit_code=1, stdout="relay stdout", stderr="relay stderr")


def test_azure_pipelines_snippet_publishes_junit_results() -> None:
    assert "continueOnError: true" in AZURE_PIPELINES_SNIPPET
    assert "PublishTestResults@2" in AZURE_PIPELINES_SNIPPET
    assert "*.xml" in AZURE_PIPELINES_SNIPPET


class JUnitReportTests(unittest.TestCase):
    def test_junit_xml_distinguishes_failure_and_error(self) -> None:
        report = GateReport(
            suite_name="relay-quality",
            results=(
                GateResult(
                    name="lint",
                    command=("ruff", "check", "src"),
                    outcome=GateOutcome.FAILED,
                    summary="lint found problems",
                    detail="F401 unused import",
                    exit_code=1,
                ),
                GateResult(
                    name="types",
                    command=("pyright", "src"),
                    outcome=GateOutcome.ERRORED,
                    summary="types could not start",
                    detail="No such file or directory: pyright",
                    exit_code=None,
                ),
            ),
        )

        xml_text = report.to_junit_xml()

        self.assertIn('<testsuite name="relay-quality" tests="2" failures="1" errors="1"', xml_text)
        self.assertIn("<failure", xml_text)
        self.assertIn("<error", xml_text)


def test_version_is_exposed() -> None:
    assert __version__
