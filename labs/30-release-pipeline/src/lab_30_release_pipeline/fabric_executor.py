"""A Fabric-style verification task that checks every deployed relay host."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import urlopen


@dataclass(frozen=True)
class HostVerification:
    """The result of checking one deployed host."""

    host: str
    ready: bool
    digest: str
    message: str


@dataclass(frozen=True)
class VerificationSummary:
    """The aggregate result for all checked hosts."""

    expected_digest: str
    results: tuple[HostVerification, ...]

    @property
    def failures(self) -> tuple[HostVerification, ...]:
        """Return every failing host result."""

        return tuple(
            result
            for result in self.results
            if not result.ready or result.digest != self.expected_digest
        )

    @property
    def all_passed(self) -> bool:
        """Return True when every host matches the expected digest."""

        return not self.failures


class VerificationError(RuntimeError):
    """Raised when at least one host fails release verification."""

    def __init__(self, summary: VerificationSummary) -> None:
        hosts = ", ".join(result.host for result in summary.failures)
        super().__init__(f"release verification failed for: {hosts}")
        self.summary = summary


class HostProbe(Protocol):
    """A small protocol for host-level verification."""

    def verify(self, host: str, expected_digest: str) -> HostVerification:
        """Return the verification result for one host."""

        ...


@dataclass
class HttpHostProbe:
    """Verify relay hosts by reading their health and metadata endpoints."""

    timeout: float = 5.0

    def verify(self, host: str, expected_digest: str) -> HostVerification:
        ready_payload = self._load_json(f"http://{host}/readyz")
        metadata_payload = self._load_json(f"http://{host}/metadata")
        ready = str(ready_payload.get("status")) == "ready"
        digest = str(metadata_payload.get("release_digest", ""))
        if ready and digest == expected_digest:
            return HostVerification(host=host, ready=True, digest=digest, message="ok")
        message = "digest mismatch" if digest != expected_digest else "host is not ready"
        return HostVerification(host=host, ready=ready, digest=digest, message=message)

    def _load_json(self, url: str) -> dict[str, Any]:
        try:
            with urlopen(url, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return json.loads(error.read().decode("utf-8"))


@dataclass
class FabricExecutorTask:
    """Verify each host exactly once and fail the stage on any mismatch."""

    probe: HostProbe

    @classmethod
    def from_http(cls, timeout: float = 5.0) -> FabricExecutorTask:
        """Build a task that verifies hosts over HTTP."""

        return cls(probe=HttpHostProbe(timeout=timeout))

    def verify(self, hosts: Sequence[str], expected_digest: str) -> VerificationSummary:
        """Check every host against *expected_digest*."""

        if not hosts:
            raise ValueError("hosts must not be empty")
        results = tuple(self.probe.verify(host, expected_digest) for host in hosts)
        summary = VerificationSummary(expected_digest=expected_digest, results=results)
        if not summary.all_passed:
            raise VerificationError(summary)
        return summary


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Verify relay hosts after deploy")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--hosts", required=True)
    verify.add_argument("--expected-digest", required=True)

    args = parser.parse_args(argv)
    task = FabricExecutorTask(probe=HttpHostProbe())
    hosts = [host.strip() for host in args.hosts.split(",") if host.strip()]
    summary = task.verify(hosts, args.expected_digest)
    print(json.dumps([result.__dict__ for result in summary.results], sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
