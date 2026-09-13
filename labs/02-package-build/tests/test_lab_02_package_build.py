"""Tests for building and inspecting relayctl."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lab_02_package_build import __version__
from lab_02_package_build import binary as binary_module
from lab_02_package_build.binary import ExecutableFormat, executable_format, readelf_headers
from lab_02_package_build.cli import main

ROOT = Path(__file__).parents[1]


def test_detects_elf_from_its_magic(tmp_path: Path) -> None:
    executable = tmp_path / "relayctl"
    executable.write_bytes(b"\x7fELF" + bytes(60))

    assert executable_format(executable) is ExecutableFormat.ELF


def test_detects_pe_from_both_signatures(tmp_path: Path) -> None:
    executable = tmp_path / "relayctl.exe"
    contents = bytearray(132)
    contents[:2] = b"MZ"
    contents[0x3C:0x40] = (128).to_bytes(4, byteorder="little")
    contents[128:132] = b"PE\x00\x00"
    executable.write_bytes(contents)

    assert executable_format(executable) is ExecutableFormat.PE


@pytest.mark.parametrize("contents", [b"not executable", b"MZ", b"MZ" + bytes(62)])
def test_rejects_missing_or_incomplete_signatures(tmp_path: Path, contents: bytes) -> None:
    candidate = tmp_path / "candidate"
    candidate.write_bytes(contents)

    assert executable_format(candidate) is ExecutableFormat.UNKNOWN


def test_readelf_rejects_a_non_elf_file(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.exe"
    candidate.write_bytes(b"MZ")

    with pytest.raises(ValueError, match="requires an ELF"):
        readelf_headers(candidate)


def test_readelf_reports_a_missing_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = tmp_path / "relayctl"
    candidate.write_bytes(b"\x7fELF")

    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(binary_module.subprocess, "run", missing)

    with pytest.raises(FileNotFoundError, match="not found: missing-readelf"):
        readelf_headers(candidate, readelf="missing-readelf")


def test_readelf_reports_an_inspection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "relayctl"
    candidate.write_bytes(b"\x7fELF")
    failed = subprocess.CompletedProcess[str](
        args=["readelf"],
        returncode=1,
        stdout="",
        stderr="unsupported ELF class",
    )
    monkeypatch.setattr(binary_module.subprocess, "run", lambda *args, **kwargs: failed)

    with pytest.raises(binary_module.ReadelfError, match="exited 1: unsupported ELF class"):
        readelf_headers(candidate)


def test_cli_reports_the_detected_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = tmp_path / "relayctl"
    candidate.write_bytes(b"\x7fELF")

    assert main(["inspect", os.fspath(candidate)]) == 0
    assert capsys.readouterr().out == "ELF\n"


def test_cli_rejects_elf_headers_for_another_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate = tmp_path / "relayctl.exe"
    candidate.write_bytes(b"MZ")

    with pytest.raises(SystemExit) as raised:
        main(["inspect", "--headers", os.fspath(candidate)])

    assert raised.value.code == 2
    assert "--headers requires an ELF executable" in capsys.readouterr().err


@pytest.fixture(scope="module")
def bundled_relayctl(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("pyinstaller")
    name = "relayctl.exe" if os.name == "nt" else "relayctl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--name",
            "relayctl",
            "--distpath",
            os.fspath(output / "dist"),
            "--workpath",
            os.fspath(output / "build"),
            "--specpath",
            os.fspath(output),
            "--paths",
            os.fspath(ROOT / "src"),
            os.fspath(ROOT / "src/lab_02_package_build/__main__.py"),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    return output / "dist" / name


def test_bundled_command_runs_without_the_source_tree(bundled_relayctl: Path) -> None:
    result = subprocess.run(
        [bundled_relayctl, "--version"],
        cwd=bundled_relayctl.parent,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == f"relayctl {__version__}"


def test_bundled_command_has_the_native_format(bundled_relayctl: Path) -> None:
    if sys.platform.startswith("linux"):
        assert executable_format(bundled_relayctl) is ExecutableFormat.ELF
        headers = readelf_headers(bundled_relayctl)
        assert "ELF Header:" in headers
        assert "Machine:" in headers
    elif os.name == "nt":
        assert executable_format(bundled_relayctl) is ExecutableFormat.PE
    else:
        pytest.skip("the lab targets Linux and Windows")


def test_copier_template_generates_the_relay_contract(tmp_path: Path) -> None:
    copier = shutil.which("copier")
    assert copier is not None
    destination = tmp_path / "generated-relay"

    subprocess.run(
        [
            copier,
            "copy",
            "--defaults",
            "--data",
            "project_name=relay-generated",
            "--data",
            "package_name=relay_generated",
            os.fspath(ROOT / "relay-template"),
            os.fspath(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    metadata = (destination / "pyproject.toml").read_text(encoding="utf-8")
    contract = (destination / "src/relay_generated/__init__.py").read_text(encoding="utf-8")
    assert 'name = "relay-generated"' in metadata
    assert 'TASK_RESOURCE = "/tasks"' in contract
    assert '"queued", "running", "succeeded", "failed"' in contract
    assert (destination / ".copier-answers.yml").is_file()
