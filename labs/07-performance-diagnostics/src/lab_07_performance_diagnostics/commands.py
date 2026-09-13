"""Side-effect-free Linux diagnostic command plans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DiagnosticPlan:
    """Commands an operator can inspect before running with required privilege."""

    argv: tuple[str, ...]
    purpose: str

    @classmethod
    def gdb_run(cls, executable: Path, *arguments: str) -> DiagnosticPlan:
        return cls(("gdb", "--args", str(executable), *arguments), "debug a new process")

    @classmethod
    def gdb_build(cls, source: Path, output: Path) -> DiagnosticPlan:
        return cls(
            (
                "cc",
                "-g3",
                "-Og",
                "-fno-omit-frame-pointer",
                "-Wall",
                "-Wextra",
                "-o",
                str(output),
                str(source),
            ),
            "build a native probe with debugging information",
        )

    @classmethod
    def gdb_attach(cls, pid: int) -> DiagnosticPlan:
        return cls(("gdb", "-p", _pid(pid)), "attach to a running process")

    @classmethod
    def strace_attach(cls, pid: int) -> DiagnosticPlan:
        return cls(
            (
                "strace",
                "-ff",
                "-tt",
                "-T",
                "-e",
                "trace=network,read,write",
                "-p",
                _pid(pid),
            ),
            "trace relay system calls",
        )

    @classmethod
    def strace_run(cls, *command: str) -> DiagnosticPlan:
        if not command:
            raise ValueError("a command is required")
        return cls(
            (
                "strace",
                "-ff",
                "-tt",
                "-T",
                "-e",
                "trace=network,read,write,openat,close",
                "--",
                *command,
            ),
            "trace a new relay process",
        )

    @classmethod
    def lsof_process(cls, pid: int) -> DiagnosticPlan:
        return cls(("lsof", "-nP", "-p", _pid(pid)), "list open files and sockets")

    @classmethod
    def perf_record(cls, pid: int, seconds: int = 10) -> DiagnosticPlan:
        if seconds < 1:
            raise ValueError("seconds must be positive")
        return cls(
            (
                "perf",
                "record",
                "-g",
                "--call-graph",
                "dwarf",
                "-p",
                _pid(pid),
                "--",
                "sleep",
                str(seconds),
            ),
            "sample on-CPU stacks",
        )

    @classmethod
    def perf_stat(cls, *command: str) -> DiagnosticPlan:
        if not command:
            raise ValueError("a command is required")
        return cls(("perf", "stat", "-d", "--", *command), "count hardware and software events")

    @classmethod
    def ftrace_record(cls, pid: int, seconds: int = 5) -> DiagnosticPlan:
        if seconds < 1:
            raise ValueError("seconds must be positive")
        return cls(
            (
                "trace-cmd",
                "record",
                "-p",
                "function_graph",
                "-P",
                _pid(pid),
                "-o",
                "relay-ftrace.dat",
                "sleep",
                str(seconds),
            ),
            "record kernel function-graph events",
        )

    @classmethod
    def ftrace_report(cls, recording: Path = Path("relay-ftrace.dat")) -> DiagnosticPlan:
        return cls(
            ("trace-cmd", "report", "-i", str(recording)),
            "decode a trace-cmd recording",
        )

    @classmethod
    def proc_status(cls, pid: int) -> tuple[Path, ...]:
        root = Path("/proc") / _pid(pid)
        return (root / "status", root / "fd", root / "maps", root / "stack")


def _pid(value: int) -> str:
    if value < 1:
        raise ValueError("pid must be positive")
    return str(value)
