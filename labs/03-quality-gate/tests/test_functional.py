"""Functional tests for the relay quality gate.

These tests drive the public ``GateSuite`` with the real ``SubprocessRunner``.
Each gate starts an actual child Python process, so the exit-code mapping and
the JUnit XML are checked the way a CI job would use them.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from lab_03_quality_gate import GateCommand, GateOutcome, GateSuite, SubprocessRunner


def python_gate(name: str, script: str) -> GateCommand:
    return GateCommand(name, (sys.executable, "-c", script), f"Run the {name} check")


def junit_cases(xml_text: str) -> dict[str, list[str]]:
    suite = ET.fromstring(xml_text)
    return {case.attrib["name"]: [child.tag for child in case] for case in suite.iter("testcase")}


def test_passing_and_failing_checks_produce_a_findings_report() -> None:
    suite = GateSuite(
        suite_name="relay-quality",
        commands=(
            python_gate("format", "print('relay formatted')"),
            python_gate(
                "lint",
                "import sys; sys.stderr.write('F401 unused import'); sys.exit(1)",
            ),
        ),
    )

    report = suite.run(SubprocessRunner())

    assert report.exit_code == 1
    assert [result.outcome for result in report.results] == [
        GateOutcome.PASSED,
        GateOutcome.FAILED,
    ]
    assert report.results[1].detail == "F401 unused import"
    root = ET.fromstring(report.to_junit_xml())
    assert root.attrib["failures"] == "1"
    assert root.attrib["errors"] == "0"
    assert junit_cases(report.to_junit_xml()) == {"format": ["system-out"], "lint": ["failure"]}


def test_missing_checker_turns_the_whole_run_into_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-checker"
    suite = GateSuite(
        suite_name="relay-quality",
        commands=(
            python_gate("lint", "import sys; sys.exit(1)"),
            GateCommand("types", (str(missing), "src"), "Check relay types"),
        ),
    )

    report = suite.run(SubprocessRunner())

    assert report.exit_code == 2
    assert report.results[1].outcome is GateOutcome.ERRORED
    assert report.results[1].exit_code is None
    assert "is missing" in report.results[1].summary
    assert junit_cases(report.to_junit_xml()) == {"lint": ["failure"], "types": ["error"]}


def test_rejected_invocation_of_a_real_tool_is_an_error_not_a_finding() -> None:
    suite = GateSuite(
        suite_name="relay-quality",
        commands=(
            GateCommand(
                "test",
                (sys.executable, "--no-such-relay-option"),
                "Run relay tests",
            ),
        ),
    )

    report = suite.run(SubprocessRunner())

    assert report.exit_code == 2
    assert report.results[0].outcome is GateOutcome.ERRORED
    assert report.results[0].summary == "test rejected the invocation"
    assert "--no-such-relay-option" in report.results[0].detail
