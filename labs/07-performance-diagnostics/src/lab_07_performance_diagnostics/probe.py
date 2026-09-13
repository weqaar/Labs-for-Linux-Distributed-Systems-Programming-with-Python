"""Observable process used by the strace, lsof, procfs, and perf exercises."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from pathlib import Path


def cpu_work(iterations: int) -> int:
    """Deterministic CPU work with a recognizable profile and checksum."""

    if iterations < 1:
        raise ValueError("iterations must be positive")
    checksum = 0
    for value in range(iterations):
        checksum = (checksum * 33 + value) % 1_000_003
    return checksum


def wait_with_resources(seconds: float, marker: Path) -> dict[str, object]:
    """Hold a file and listening socket open long enough to inspect the process."""

    if seconds <= 0:
        raise ValueError("seconds must be positive")
    marker.parent.mkdir(parents=True, exist_ok=True)
    with marker.open("w", encoding="utf-8") as output:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            details: dict[str, object] = {
                "pid": os.getpid(),
                "marker": str(marker),
                "port": listener.getsockname()[1],
            }
            output.write(json.dumps(details))
            output.flush()
            print(json.dumps(details), flush=True)
            time.sleep(seconds)
            return details


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    cpu = subparsers.add_parser("cpu", help="run an on-CPU workload")
    cpu.add_argument("--iterations", type=int, default=2_000_000)
    wait = subparsers.add_parser("wait", help="hold a file and socket open")
    wait.add_argument("--seconds", type=float, default=30.0)
    wait.add_argument("--marker", type=Path, default=Path("relay-probe.json"))
    arguments = parser.parse_args()
    if arguments.mode == "cpu":
        print(cpu_work(arguments.iterations))
    else:
        wait_with_resources(arguments.seconds, arguments.marker)


if __name__ == "__main__":
    main()
