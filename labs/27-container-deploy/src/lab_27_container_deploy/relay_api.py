"""A small relay HTTP shape used by the deployment checkpoint."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass(frozen=True)
class RelayTask:
    """A submitted relay task."""

    task_id: str
    task: str
    state: str = "accepted"


@dataclass
class RelayApplication:
    """A compact relay app with health endpoints used by the deployment artifacts."""

    ready: bool = True
    draining: bool = False
    _next_id: int = 1
    tasks: dict[str, RelayTask] = field(default_factory=dict)

    def handle_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path == "/livez":
            return HTTPStatus.OK, {"status": "alive"}
        if method == "GET" and path == "/readyz":
            if self.ready and not self.draining:
                return HTTPStatus.OK, {"status": "ready"}
            return HTTPStatus.SERVICE_UNAVAILABLE, {"status": "draining"}
        if method == "POST" and path == "/tasks":
            payload = body if body is not None else {}
            task_name = str(payload.get("task", "")).strip()
            if not task_name:
                return HTTPStatus.BAD_REQUEST, {"error": "task is required"}
            task_id = f"relay-{self._next_id:04d}"
            self._next_id += 1
            task = RelayTask(task_id=task_id, task=task_name)
            self.tasks[task_id] = task
            return HTTPStatus.ACCEPTED, asdict(task)
        return HTTPStatus.NOT_FOUND, {"error": "not found"}


def build_handler(application: RelayApplication) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to *application*."""

    class RelayHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def _dispatch(self) -> None:
            body: dict[str, Any] | None = None
            if self.command == "POST":
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length) if content_length else b"{}"
                body = json.loads(raw_body.decode("utf-8"))
            status, payload = application.handle_request(self.command, self.path, body)
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return RelayHandler


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Run the relay checkpoint service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), build_handler(RelayApplication()))
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
