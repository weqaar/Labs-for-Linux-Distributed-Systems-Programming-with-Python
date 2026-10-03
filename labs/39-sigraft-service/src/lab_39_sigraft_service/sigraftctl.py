"""The sigraftctl client for shared job APIs over HTTP or WebSocket."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Generator
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect
from websockets.typing import Subprotocol


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

    def close(self) -> None:
        """Release transport resources; HTTP requests already close individually."""

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


class WebSocketClient(SigRaftClient):
    """Use one connection for commands and one optional status subscription.

    Writes are never retried automatically. A disconnect after submission leaves
    the outcome uncertain; reconnecting does not prove whether it was accepted.
    """

    def __init__(self, base_url: str, *, token: str) -> None:
        parsed = urlsplit(base_url)
        scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(parsed.scheme)
        if scheme is None or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("use a WebSocket server URL without credentials")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/", "/ws"}:
            raise ValueError("WebSocket URL must refer to /ws without query or fragment")
        if scheme == "ws":
            try:
                loopback = ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise ValueError("remote WebSocket connections require wss://")
        if not 32 <= len(token) <= 256 or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError("token must contain 32 to 256 non-space ASCII characters")
        super().__init__(urlunsplit((scheme, parsed.netloc, "/ws", "", "")))
        self._token = token
        self._connection: ClientConnection | None = None
        self._next_id = 0

    def close(self) -> None:
        """Close a persistent connection and its receiver thread."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _connect(self) -> ClientConnection:
        if self._connection is None:
            self._connection = connect(
                self.base_url,
                subprotocols=[Subprotocol("sigraft.jobs.v1")],
                additional_headers={"Authorization": f"Bearer {self._token}"},
                proxy=None,
                max_size=16384,
                max_queue=16,
                compression=None,
                open_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=2,
            )
        return self._connection

    def _start(self, message: dict[str, Any]) -> str:
        self._next_id += 1
        identifier = str(self._next_id)
        self._connect().send(json.dumps({"id": identifier, **message}))
        return identifier

    def _receive(self, identifier: str, *, timeout: float | None = 5) -> dict[str, Any]:
        raw = self._connect().recv(timeout=timeout)
        message = json.loads(raw)
        if not isinstance(message, dict) or message.get("id") != identifier:
            raise ValueError("unexpected WebSocket response")
        return message

    def _data(self, identifier: str) -> dict[str, Any]:
        response = self._receive(identifier)
        status = response.get("status")
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError("WebSocket response is missing its status")
        if status >= 400:
            detail = response.get("error", response.get("data"))
            raise ValueError(f"WebSocket operation failed with status {status}: {detail}")
        data = response.get("data")
        if not isinstance(data, dict):
            raise ValueError("WebSocket response data must be an object")
        return data

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        scopes: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Use the server's credential scopes; caller scopes cannot grant access."""
        return self._data(
            self._start({"operation": "request", "method": method, "path": path, "body": payload})
        )

    def watch(self, task_id: str) -> Generator[dict[str, Any], None, None]:
        """Yield the current snapshot and changes until success or failure.

        Reconnect explicitly to obtain a new snapshot after a disconnect. This
        service has no retained event log and makes no replay guarantee.
        """
        identifier = self._start({"operation": "watch", "task_id": task_id})
        try:
            self._data(identifier)
            previous = -1
            epoch = None
            while True:
                message = self._receive(identifier, timeout=None)
                sequence, current_epoch = message.get("sequence"), message.get("epoch")
                job = message.get("job")
                if (
                    message.get("type") != "event"
                    or isinstance(sequence, bool)
                    or not isinstance(sequence, int)
                    or sequence <= previous
                    or not isinstance(current_epoch, str)
                    or (epoch is not None and epoch != current_epoch)
                    or not isinstance(job, dict)
                    or job.get("task_id") != task_id
                    or job.get("state") not in {"queued", "running", "succeeded", "failed"}
                ):
                    raise ValueError("invalid or out-of-order job event")
                previous, epoch = sequence, current_epoch
                yield message
                if job["state"] in {"succeeded", "failed"}:
                    return
        finally:
            self.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interact with the SigRaft service")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--transport", choices=["http", "websocket"], default="http")
    parser.add_argument("--token-file", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit")
    submit.add_argument("task")
    submit.add_argument("--checkpoint", type=int)

    status = subparsers.add_parser("status")
    status.add_argument("task_id")
    status.add_argument("--watch", action="store_true")

    subparsers.add_parser("metadata")
    graphql = subparsers.add_parser("graphql")
    graphql.add_argument("query")
    graphql.add_argument("--scope", action="append", default=[])

    args = parser.parse_args(argv)
    watch = args.command == "status" and args.watch
    if watch and args.transport != "websocket":
        parser.error("--watch requires --transport websocket")
    if (args.transport == "websocket") != (args.token_file is not None):
        parser.error("--token-file is required only for WebSocket transport")
    client: SigRaftClient | None = None
    try:
        if args.transport == "websocket":
            if args.token_file.stat().st_mode & 0o077:
                raise ValueError("token file must not be accessible to group or others")
            if args.token_file.stat().st_size > 257:
                raise ValueError("token file is too large")
            client = WebSocketClient(
                args.base_url, token=args.token_file.read_text(encoding="utf-8").strip()
            )
        else:
            client = SigRaftClient(args.base_url)
        if watch:
            assert isinstance(client, WebSocketClient)
            for event in client.watch(args.task_id):
                print(json.dumps(event, sort_keys=True), flush=True)
        elif args.command == "submit":
            print(json.dumps(client.submit(args.task, args.checkpoint).__dict__, sort_keys=True))
        elif args.command == "status":
            print(json.dumps(client.status(args.task_id).__dict__, sort_keys=True))
        elif args.command == "graphql":
            print(
                json.dumps(client.graphql(args.query, scopes=frozenset(args.scope)), sort_keys=True)
            )
        else:
            print(json.dumps(client.metadata(), sort_keys=True))
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, URLError, WebSocketException, ValueError, TimeoutError) as error:
        print(f"sigraftctl: {error}. No automatic retry was attempted.", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
