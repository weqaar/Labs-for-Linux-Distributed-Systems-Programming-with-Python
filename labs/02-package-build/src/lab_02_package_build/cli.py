"""Command-line interface bundled as relayctl."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from lab_02_package_build import __version__
from lab_02_package_build.binary import ExecutableFormat, executable_format, readelf_headers


def parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    command = argparse.ArgumentParser(prog="relayctl")
    command.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = command.add_subparsers(dest="command", required=True)

    inspect = subcommands.add_parser("inspect", help="identify an executable")
    inspect.add_argument("path", help="path to an ELF or PE executable")
    inspect.add_argument(
        "--headers",
        action="store_true",
        help="print readelf file and program headers for an ELF executable",
    )
    return command


def main(argv: Sequence[str] | None = None) -> int:
    """Run relayctl."""
    arguments = parser().parse_args(argv)
    detected = executable_format(arguments.path)
    print(detected.value)

    if arguments.headers:
        if detected is not ExecutableFormat.ELF:
            parser().error("--headers requires an ELF executable")
        print(readelf_headers(arguments.path), end="")
    return 0
