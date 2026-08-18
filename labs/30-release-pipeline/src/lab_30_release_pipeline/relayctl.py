"""A compact client contract for the relay REST API."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RelayCtlTask:
    """A task payload returned by the relay API."""

    task_id: str
    task: str
    state: str
    checkpoint: int | None


class RelayCtlClient:
    """A tiny relay client used by tests and examples."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def submit(self, task: str, checkpoint: int | None = None) -> RelayCtlTask:
        """Submit a task to the relay API."""

        payload: dict[str, Any] = {"task": task}
        if checkpoint is not None:
            payload["checkpoint"] = checkpoint
        data = self._request("POST", "/tasks", payload)
        return RelayCtlTask(
            task_id=str(data["task_id"]),
            task=str(data["task"]),
            state=str(data["state"]),
            checkpoint=int(data["checkpoint"]) if data["checkpoint"] is not None else None,
        )

    def status(self, task_id: str) -> RelayCtlTask:
        """Return the status for one relay task."""

        data = self._request("GET", f"/tasks/{task_id}")
        return RelayCtlTask(
            task_id=str(data["task_id"]),
            task=str(data["task"]),
            state=str(data["state"]),
            checkpoint=int(data["checkpoint"]) if data["checkpoint"] is not None else None,
        )

    def metadata(self) -> dict[str, Any]:
        """Return the relay metadata endpoint."""

        return self._request("GET", "/metadata")

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Interact with the compact relay API")
    parser.add_argument("--base-url", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit")
    submit.add_argument("task")
    submit.add_argument("--checkpoint", type=int)

    status = subparsers.add_parser("status")
    status.add_argument("task_id")

    subparsers.add_parser("metadata")

    args = parser.parse_args(argv)
    client = RelayCtlClient(args.base_url)
    if args.command == "submit":
        result = client.submit(args.task, args.checkpoint)
        print(json.dumps(result.__dict__, sort_keys=True))
        return 0
    if args.command == "status":
        result = client.status(args.task_id)
        print(json.dumps(result.__dict__, sort_keys=True))
        return 0
    print(json.dumps(client.metadata(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
