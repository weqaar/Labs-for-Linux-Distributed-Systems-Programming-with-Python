"""Relay checkpoint 03: honest JUnit quality gate evidence."""

from __future__ import annotations

from .quality import (
    AZURE_PIPELINES_SNIPPET,
    CommandRunner,
    GateCommand,
    GateOutcome,
    GateReport,
    GateResult,
    GateSuite,
    ProcessResult,
    SubprocessRunner,
    relay_quality_suite,
    run_gate,
)

__version__ = "0.1.0"

__all__ = [
    "AZURE_PIPELINES_SNIPPET",
    "CommandRunner",
    "GateCommand",
    "GateOutcome",
    "GateReport",
    "GateResult",
    "GateSuite",
    "ProcessResult",
    "SubprocessRunner",
    "__version__",
    "relay_quality_suite",
    "run_gate",
]
