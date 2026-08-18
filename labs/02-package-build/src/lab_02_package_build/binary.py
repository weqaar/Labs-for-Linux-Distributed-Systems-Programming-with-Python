"""Identify executable formats and inspect ELF metadata."""

from __future__ import annotations

import os
import subprocess
from enum import Enum
from pathlib import Path


class ExecutableFormat(Enum):
    """Executable formats produced by this lab."""

    ELF = "ELF"
    PE = "PE"
    UNKNOWN = "unknown"


class ReadelfError(RuntimeError):
    """Raised when readelf is present but cannot inspect an ELF file."""


def executable_format(path: str | os.PathLike[str]) -> ExecutableFormat:
    """Return the executable format identified from *path*'s signatures."""
    with Path(path).open("rb") as stream:
        if stream.read(4) == b"\x7fELF":
            return ExecutableFormat.ELF

        stream.seek(0)
        if stream.read(2) != b"MZ":
            return ExecutableFormat.UNKNOWN

        stream.seek(0x3C)
        offset_bytes = stream.read(4)
        if len(offset_bytes) != 4:
            return ExecutableFormat.UNKNOWN

        pe_offset = int.from_bytes(offset_bytes, byteorder="little")
        stream.seek(pe_offset)
        if stream.read(4) == b"PE\x00\x00":
            return ExecutableFormat.PE

    return ExecutableFormat.UNKNOWN


def readelf_headers(
    path: str | os.PathLike[str],
    *,
    readelf: str = "readelf",
) -> str:
    """Return the ELF file and program headers reported by readelf."""
    if executable_format(path) is not ExecutableFormat.ELF:
        raise ValueError("readelf requires an ELF executable")

    try:
        result = subprocess.run(
            [readelf, "--file-header", "--program-headers", os.fspath(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"readelf executable not found: {readelf}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or "no diagnostic"
        raise ReadelfError(f"readelf exited {result.returncode}: {detail}")
    return result.stdout
