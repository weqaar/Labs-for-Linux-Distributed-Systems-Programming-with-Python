"""A compact runnable relay REST API used by the release checkpoint."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import JSONDecodeError
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from lab_30_release_pipeline.version import __version__


@dataclass(frozen=True)
class RelayTaskRecord:
    """A stored relay task."""

    task_id: str
    task: str
    state: str
    checkpoint: int | None


@dataclass
class RelayService:
    """An in-memory relay service with health and metadata endpoints."""

    release_digest: str
    ready: bool = True
    _tasks: dict[str, RelayTaskRecord] = field(default_factory=dict)
    _next_id: int = 1

    def metadata(self) -> dict[str, str]:
        """Return deployment metadata used by release verification."""

        return {
            "service": "relay",
            "version": __version__,
            "release_digest": self.release_digest,
        }

    def submit_task(self, task: str, checkpoint: int | None = None) -> RelayTaskRecord:
        """Store a new relay task."""

        cleaned = task.strip()
        if not cleaned:
            raise ValueError("task must not be empty")
        task_id = f"relay-{self._next_id:04d}"
        self._next_id += 1
        record = RelayTaskRecord(
            task_id=task_id, task=cleaned, state="accepted", checkpoint=checkpoint
        )
        self._tasks[task_id] = record
        return record

    def get_task(self, task_id: str) -> RelayTaskRecord | None:
        """Return a stored task by id."""

        return self._tasks.get(task_id)


def build_handler(service: RelayService) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to *service*."""

    class RelayHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def _dispatch(self) -> None:
            try:
                status, payload = _route_request(
                    service, self.command, self.path, self._read_body()
                )
            except JSONDecodeError:
                status, payload = (
                    HTTPStatus.BAD_REQUEST,
                    {"error": "request body must be valid JSON"},
                )
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _read_body(self) -> dict[str, Any] | None:
            if self.command != "POST":
                return None
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length else b"{}"
            return json.loads(raw_body.decode("utf-8"))

    return RelayHandler


def _route_request(
    service: RelayService, method: str, raw_path: str, body: dict[str, Any] | None
) -> tuple[int, dict[str, Any]]:
    path = urlparse(raw_path).path
    if method == "GET" and path == "/livez":
        return HTTPStatus.OK, {"status": "alive"}
    if method == "GET" and path == "/readyz":
        if service.ready:
            return HTTPStatus.OK, {"status": "ready"}
        return HTTPStatus.SERVICE_UNAVAILABLE, {"status": "starting"}
    if method == "GET" and path == "/metadata":
        return HTTPStatus.OK, service.metadata()
    if method == "POST" and path == "/tasks":
        payload = body if body is not None else {}
        checkpoint_value = payload.get("checkpoint")
        try:
            checkpoint = int(checkpoint_value) if checkpoint_value is not None else None
            record = service.submit_task(str(payload.get("task", "")), checkpoint)
        except ValueError:
            return HTTPStatus.BAD_REQUEST, {"error": "task and checkpoint must be valid"}
        return HTTPStatus.ACCEPTED, asdict(record)
    if method == "GET" and path.startswith("/tasks/"):
        task_id = path.rsplit("/", maxsplit=1)[-1]
        record = service.get_task(task_id)
        if record is None:
            return HTTPStatus.NOT_FOUND, {"error": "task not found"}
        return HTTPStatus.OK, asdict(record)
    return HTTPStatus.NOT_FOUND, {"error": "not found"}


@dataclass
class HostedRelayServer:
    """A relay server running in a background thread for tests."""

    server: ThreadingHTTPServer
    thread: Thread

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        """Stop the hosted server."""

        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def run_server(service: RelayService, host: str = "127.0.0.1", port: int = 0) -> HostedRelayServer:
    """Run *service* in a background thread and return the host handle."""

    server = ThreadingHTTPServer((host, port), build_handler(service))
    thread = Thread(target=server.serve_forever, name="relay-server", daemon=True)
    thread.start()
    return HostedRelayServer(server=server, thread=thread)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Run the compact relay API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--digest", required=True)
    args = parser.parse_args(argv)

    hosted = run_server(RelayService(release_digest=args.digest), host=args.host, port=args.port)
    try:
        hosted.thread.join()
    finally:
        hosted.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
