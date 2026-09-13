"""The compact sigraftctl client contract for the SigRaft REST API."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SigRaftTask:
    """A task payload returned by the SigRaft service."""

    task_id: str
    action: str
    state: str
    checkpoint: int | None


class SigRaftClient:
    """A small SigRaft client used by tests and examples."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def submit(self, action: str, checkpoint: int | None = None) -> SigRaftTask:
        """Submit a task to the SigRaft service."""

        payload: dict[str, Any] = {"action": action}
        if checkpoint is not None:
            payload["checkpoint"] = checkpoint
        data = self._request("POST", "/tasks", payload)
        return SigRaftTask(
            task_id=str(data["task_id"]),
            action=str(data["action"]),
            state=str(data["state"]),
            checkpoint=int(data["checkpoint"]) if data["checkpoint"] is not None else None,
        )

    def status(self, task_id: str) -> SigRaftTask:
        """Return the status for one SigRaft task."""

        data = self._request("GET", f"/tasks/{task_id}")
        return SigRaftTask(
            task_id=str(data["task_id"]),
            action=str(data["action"]),
            state=str(data["state"]),
            checkpoint=int(data["checkpoint"]) if data["checkpoint"] is not None else None,
        )

    def metadata(self) -> dict[str, Any]:
        """Return the SigRaft metadata endpoint."""

        return self._request("GET", "/metadata")

    def graphql(
        self,
        query: str,
        *,
        variables: dict[str, object] | None = None,
        scopes: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Execute one GraphQL query or mutation."""

        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        return self._request("POST", "/graphql", payload, scopes=scopes)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        scopes: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if scopes:
            headers["X-SigRaft-Scopes"] = " ".join(sorted(scopes))
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Interact with the SigRaft service")
    parser.add_argument("--base-url", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit")
    submit.add_argument("task")
    submit.add_argument("--checkpoint", type=int)

    status = subparsers.add_parser("status")
    status.add_argument("task_id")

    subparsers.add_parser("metadata")
    graphql = subparsers.add_parser("graphql")
    graphql.add_argument("query")
    graphql.add_argument("--scope", action="append", default=[])

    args = parser.parse_args(argv)
    client = SigRaftClient(args.base_url)
    if args.command == "submit":
        result = client.submit(args.task, args.checkpoint)
        print(json.dumps(result.__dict__, sort_keys=True))
        return 0
    if args.command == "status":
        result = client.status(args.task_id)
        print(json.dumps(result.__dict__, sort_keys=True))
        return 0
    if args.command == "graphql":
        print(
            json.dumps(
                client.graphql(args.query, scopes=frozenset(args.scope)),
                sort_keys=True,
            )
        )
        return 0
    print(json.dumps(client.metadata(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
