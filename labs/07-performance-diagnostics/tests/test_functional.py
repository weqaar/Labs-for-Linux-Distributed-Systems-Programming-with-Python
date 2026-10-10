"""Functional tests for the relay performance diagnostics checkpoint.

These tests drive the lab's command-line entry points the way a reader runs
them with ``python -m``: the ``main`` functions of
``lab_07_performance_diagnostics.debug_target`` and
``lab_07_performance_diagnostics.probe``. Each test sets the process arguments,
calls ``main`` and checks the printed output, the files written and the errors
raised. One test also follows the profiling workflow from a saved profile to
the rows read back from it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from lab_07_performance_diagnostics import (
    cpu_work,
    debug_target,
    probe,
    profile_to_file,
    read_profile,
)


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    program: str,
    arguments: list[str],
    main: Callable[[], None],
) -> None:
    monkeypatch.setattr(sys, "argv", [program, *arguments])
    main()


def test_debug_target_valid_record_prints_the_retry_delay(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_main(monkeypatch, "debug_target", ["--valid"], debug_target.main)

    assert capsys.readouterr().out == "1.0\n"


def test_debug_target_default_run_reproduces_the_invalid_retry_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="task-17 has a negative attempt count"):
        run_main(monkeypatch, "debug_target", [], debug_target.main)

    assert capsys.readouterr().out == ""


def test_cpu_probe_prints_the_checksum_and_rejects_bad_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_main(monkeypatch, "probe", ["cpu", "--iterations", "5000"], probe.main)
    assert capsys.readouterr().out == f"{cpu_work(5000)}\n"

    with pytest.raises(ValueError, match="iterations must be positive"):
        run_main(monkeypatch, "probe", ["cpu", "--iterations", "0"], probe.main)
    with pytest.raises(SystemExit) as missing_mode:
        run_main(monkeypatch, "probe", [], probe.main)
    assert missing_mode.value.code == 2
    assert "required" in capsys.readouterr().err


def test_wait_probe_reports_its_pid_and_loopback_port_and_writes_the_marker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    marker = tmp_path / "probes" / "relay-probe.json"

    run_main(
        monkeypatch,
        "probe",
        ["wait", "--seconds", "0.001", "--marker", str(marker)],
        probe.main,
    )

    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(marker.read_text(encoding="utf-8"))
    assert printed["pid"] == os.getpid()
    assert printed["marker"] == str(marker)
    assert isinstance(printed["port"], int) and printed["port"] > 0

    with pytest.raises(ValueError, match="seconds must be positive"):
        run_main(monkeypatch, "probe", ["wait", "--seconds", "0"], probe.main)


def test_saved_cpu_profile_names_the_probe_workload(tmp_path: Path) -> None:
    output = tmp_path / "relay.prof"

    checksum = profile_to_file(output, cpu_work, 20_000)
    rows = read_profile(output)

    assert checksum == cpu_work(20_000)
    assert output.is_file()
    assert any(":cpu_work [" in row.function for row in rows)
    assert all(
        rows[i].cumulative_seconds >= rows[i + 1].cumulative_seconds for i in range(len(rows) - 1)
    )
