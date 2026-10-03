# SPDX-License-Identifier: Apache-2.0
"""Authenticated job commands and bounded status notifications over WebSocket.

The listener is loopback-only. A remote deployment needs a TLS proxy and its
own identity lifecycle. Credentials are read at startup; restart to revoke them.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from queue import Empty
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.sync.server import Server, ServerConnection, serve
from websockets.typing import Subprotocol

from lab_39_sigraft_service.job_events import JobSubscription
from lab_39_sigraft_service.sigraft_service import SigRaftService, _route_request

SUBPROTOCOL = Subprotocol("sigraft.jobs.v1")
SCOPES = frozenset({"tasks:read", "tasks:write"})


def load_credentials(path: Path) -> dict[str, frozenset[str]]:
    """Read a private JSON mapping from bearer tokens to server-assigned scopes.

    Tokens must contain 32 to 256 printable ASCII characters without spaces.
    No credential values are included in validation errors.
    """
    if path.stat().st_mode & 0o077:
        raise ValueError("credential file must not be accessible to group or others")
    if path.stat().st_size > 65536:
        raise ValueError("credential file exceeds 64 KiB")
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or not 1 <= len(values) <= 32:
        raise ValueError("credentials must contain between 1 and 32 token entries")
    result: dict[str, frozenset[str]] = {}
    for token, scopes in values.items():
        if (
            not isinstance(token, str)
            or not 32 <= len(token) <= 256
            or any(not 33 <= ord(char) <= 126 for char in token)
            or not isinstance(scopes, list)
            or not scopes
            or any(not isinstance(scope, str) or scope not in SCOPES for scope in scopes)
        ):
            raise ValueError("invalid token or scopes in credential file")
        result[token] = frozenset(scopes)
    return result


def _identity(request: Request, credentials: Mapping[str, frozenset[str]]) -> frozenset[str]:
    headers = request.headers.get_all("Authorization")
    if len(headers) != 1 or not headers[0].startswith("Bearer "):
        return frozenset()
    supplied = headers[0][7:].encode("utf-8")
    for token, scopes in credentials.items():
        if hmac.compare_digest(supplied, token.encode("utf-8")):
            return scopes
    return frozenset()


def decode_message(raw: str | bytes) -> dict[str, Any]:
    """Validate a bounded JSON text envelope before dispatching any command."""
    if not isinstance(raw, str):
        raise ValueError("messages must be JSON text")
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    identifier = message.get("id")
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 64:
        raise ValueError("id must contain 1 to 64 characters")
    return message


@dataclass
class WebSocketSession:
    """One authenticated connection, with at most one live job subscription."""

    service: SigRaftService
    connection: ServerConnection
    scopes: frozenset[str]
    epoch: str
    _subscription: JobSubscription | None = None
    _sender: Thread | None = None
    _stop: Event = field(default_factory=Event)

    def send(self, message: dict[str, Any]) -> None:
        """Send one complete response or event without interleaving fragments."""
        self.connection.send(json.dumps(message))

    def run(self) -> None:
        """Process bounded commands; malformed messages never execute a write."""
        seen: set[str] = set()
        try:
            for raw in self.connection:
                try:
                    message = decode_message(raw)
                except (ValueError, UnicodeError):
                    self.connection.close(1008, "invalid JSON command envelope")
                    break
                identifier = message["id"]
                if identifier in seen:
                    self.send({"id": identifier, "status": 409, "error": "duplicate request id"})
                    continue
                if len(seen) >= 1024:
                    self.connection.close(1013, "command limit reached; open a new connection")
                    break
                seen.add(identifier)
                try:
                    self.dispatch(message)
                except PermissionError:
                    self.send({"id": identifier, "status": 403, "error": "operation forbidden"})
                except KeyError:
                    self.send({"id": identifier, "status": 404, "error": "job not found"})
                except (ValueError, TypeError):
                    self.send({"id": identifier, "status": 400, "error": "invalid command"})
        except ConnectionClosed:
            pass  # The peer disconnected; cleanup must still release its mailbox.
        finally:
            self.stop_watch()

    def dispatch(self, message: dict[str, Any]) -> None:
        """Authorize each operation using server-assigned, not caller-supplied, scopes."""
        identifier = message["id"]
        operation = message.get("operation")
        if operation == "unwatch":
            self.stop_watch()
            self.send({"id": identifier, "status": 200, "data": {"watching": False}})
            return
        if operation == "watch":
            if "tasks:read" not in self.scopes:
                raise PermissionError
            task_id = message.get("task_id")
            if not isinstance(task_id, str):
                raise ValueError("task_id must be text")
            if self._subscription is not None:
                raise ValueError("unwatch before starting another subscription")
            subscription = self.service.subscribe_task(task_id)
            self._subscription = subscription
            self._stop.clear()
            self.send({"id": identifier, "status": 200, "data": {"watching": True}})
            self._sender = Thread(
                target=self._send_events, args=(identifier, subscription), daemon=True
            )
            self._sender.start()
            return
        if operation != "request":
            raise ValueError("unknown operation")
        method, path = message.get("method"), message.get("path")
        body = message.get("body")
        if not isinstance(method, str) or not isinstance(path, str):
            raise ValueError("method and path must be text")
        if body is not None and not isinstance(body, dict):
            raise ValueError("body must be an object")
        if (method, path) == ("POST", "/tasks"):
            required = "tasks:write"
        elif (method, path) == ("GET", "/metadata") or (
            method == "GET" and path.startswith("/tasks/") and path.count("/") == 2
        ):
            required = "tasks:read"
        elif (method, path) == ("POST", "/graphql"):
            required = None  # Resolvers authorize queries and mutations separately.
        else:
            raise ValueError("unsupported route")
        if required is not None and required not in self.scopes:
            raise PermissionError
        status, data = _route_request(self.service, method, path, body, scopes=self.scopes)
        self.send({"id": identifier, "status": int(status), "data": data})

    def _send_events(self, identifier: str, subscription: JobSubscription) -> None:
        try:
            while not self._stop.is_set():
                if subscription.overflow.is_set():
                    self.connection.close(1013, "observer too slow; reconnect for a snapshot")
                    return
                try:
                    event = subscription.events.get(timeout=0.1)
                except Empty:
                    continue
                self.send(
                    {
                        "id": identifier,
                        "type": "event",
                        "epoch": self.epoch,
                        "sequence": event.sequence,
                        "job": event.job,
                    }
                )
        except ConnectionClosed:
            return

    def stop_watch(self) -> None:
        """Release the mailbox and join its sender when an operation ends."""
        self._stop.set()
        if self._subscription is not None:
            self.service.unsubscribe_task(self._subscription)
            self._subscription = None
        if self._sender is not None:
            self._sender.join(timeout=1)
            if self._sender.is_alive():
                self.connection.close(1013, "writer stalled")
                self._sender.join(timeout=5)
            if self._sender.is_alive():
                raise RuntimeError("WebSocket writer did not stop")
            self._sender = None


@dataclass
class HostedWebSocketServer:
    """A separately bound optional listener sharing one SigRaft service."""

    server: Server
    thread: Thread
    connections: dict[ServerConnection, Event]
    lock: Lock
    stopping: Event

    @property
    def base_url(self) -> str:
        """Return the loopback URL, including an ephemeral port chosen by the OS."""
        return f"ws://127.0.0.1:{self.server.socket.getsockname()[1]}/ws"

    def close(self) -> None:
        """Stop admission and close active sessions, including idle subscribers."""
        self.stopping.set()
        self.server.shutdown()
        with self.lock:
            connections = tuple(self.connections.items())
        for connection, _ in connections:
            connection.close(1001, "service shutting down")
        for _, finished in connections:
            if not finished.wait(timeout=5):
                raise RuntimeError("WebSocket session did not stop")
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("WebSocket listener did not stop")


def run_websocket_server(
    service: SigRaftService,
    *,
    credentials: Mapping[str, frozenset[str]],
    port: int = 0,
) -> HostedWebSocketServer:
    """Start a loopback-only listener without changing the existing HTTP server.

    Authentication happens before upgrade. One credential represents a local
    lab principal, not a tenant isolation or expiring identity implementation.
    """
    if not credentials or any(
        not scopes or not scopes <= SCOPES for scopes in credentials.values()
    ):
        raise ValueError("credentials must grant known nonempty scopes")
    connections: dict[ServerConnection, Event] = {}
    lock = Lock()
    epoch = str(uuid4())
    stopping = Event()

    def authorize(connection: ServerConnection, request: Request) -> Response | None:
        if request.path != "/ws":
            return connection.respond(HTTPStatus.NOT_FOUND, "unknown WebSocket endpoint\n")
        if not _identity(request, credentials):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "valid bearer token required\n")
        return None

    def handle(connection: ServerConnection) -> None:
        finished = Event()
        with lock:
            if stopping.is_set() or len(connections) >= 32:
                connection.close(1013, "connection capacity reached")
                return
            connections[connection] = finished
        try:
            assert connection.request is not None
            WebSocketSession(
                service, connection, _identity(connection.request, credentials), epoch
            ).run()
        finally:
            with lock:
                connections.pop(connection, None)
                finished.set()

    server = serve(
        handle,
        "127.0.0.1",
        port,
        subprotocols=[SUBPROTOCOL],
        origins=[None],
        process_request=authorize,
        max_size=16384,
        max_queue=16,
        compression=None,
        open_timeout=5,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=2,
    )
    thread = Thread(target=server.serve_forever, name="sigraft-websocket", daemon=True)
    thread.start()
    return HostedWebSocketServer(server, thread, connections, lock, stopping)
