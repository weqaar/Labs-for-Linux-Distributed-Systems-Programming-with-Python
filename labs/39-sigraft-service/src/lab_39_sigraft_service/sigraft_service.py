"""The runnable SigRaft REST service with telemetry and transactional reload."""

from __future__ import annotations

import argparse
import json
import os
import signal
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import JSONDecodeError
from pathlib import Path
from threading import RLock, Thread
from time import monotonic_ns
from typing import Any
from urllib.parse import urlparse

from opentelemetry.trace import SpanKind, Status, StatusCode, set_span_in_context

from lab_39_sigraft_service.analytics_service import AnalyticsRuntime
from lab_39_sigraft_service.config import (
    DEFAULT_CONFIG,
    ConfigManager,
    ConfigWatcher,
    RequestLimits,
)
from lab_39_sigraft_service.graphql_api import SigRaftGraphQL, execute_http_payload
from lab_39_sigraft_service.redfish import RedfishInventory, default_inventory
from lab_39_sigraft_service.scheduler import (
    SchedulerNode,
    SchedulerResources,
    SigRaftScheduler,
)
from lab_39_sigraft_service.telemetry import (
    PROPAGATOR,
    TelemetryRuntime,
    emit_request_log,
)
from lab_39_sigraft_service.version import __version__


@dataclass(frozen=True)
class SigRaftTask:
    """A stored SigRaft task using the contract established by earlier labs."""

    task_id: str
    action: str
    state: str
    checkpoint: int | None


@dataclass
class SigRaftService:
    """An in-memory SigRaft service with observable HTTP boundaries."""

    release_digest: str
    config_manager: ConfigManager | None = None
    telemetry: TelemetryRuntime | None = None
    request_limits: RequestLimits | None = None
    watcher: ConfigWatcher | None = None
    analytics: AnalyticsRuntime | None = None
    redfish: RedfishInventory | None = None
    scheduler: SigRaftScheduler | None = None
    graphql: SigRaftGraphQL = field(init=False)
    ready: bool = True
    _tasks: dict[str, SigRaftTask] = field(default_factory=dict)
    _next_id: int = 1
    _task_lock: RLock = field(default_factory=RLock)
    _request_counter: Any = field(init=False)
    _request_duration: Any = field(init=False)

    def __post_init__(self) -> None:
        manager = self.config_manager or ConfigManager.from_bytes(DEFAULT_CONFIG)
        limits = self.request_limits or RequestLimits(manager.snapshot.settings.requests)
        telemetry = self.telemetry or TelemetryRuntime.in_memory(
            version=__version__,
            environment="test",
            instance_id="sigraft-test",
            release_digest=self.release_digest,
            sample_ratio=manager.snapshot.settings.telemetry.sample_ratio,
        )
        self.config_manager = manager
        self.request_limits = limits
        self.telemetry = telemetry
        analytics = self.analytics or AnalyticsRuntime(manager.snapshot.settings.analysis)
        self.analytics = analytics
        self.redfish = self.redfish or default_inventory()
        self.scheduler = self.scheduler or SigRaftScheduler()
        self.graphql = SigRaftGraphQL(self)
        manager.register(limits)
        manager.register(telemetry.sampler)
        manager.register(analytics)
        meter = telemetry.meter
        self._request_counter = meter.create_counter("sigraft.http.requests", unit="{request}")
        self._request_duration = meter.create_histogram("sigraft.http.request.duration", unit="ms")

    def metadata(self) -> dict[str, Any]:
        """Return non-secret deployment and reload metadata."""

        assert self.config_manager is not None
        return {
            "service": "sigraft",
            "version": __version__,
            "release_digest": self.release_digest,
            "configuration": self.config_manager.status.as_dict(),
            "analysis": self.analytics.metadata() if self.analytics else None,
        }

    def submit_task(self, action: str, checkpoint: int | None = None) -> SigRaftTask:
        """Store a new task in the queued state."""

        assert self.request_limits is not None
        cleaned = action.strip()
        if not cleaned:
            raise ValueError("action must not be empty")
        if len(cleaned) > self.request_limits.current.max_action_chars:
            raise ValueError("action exceeds the configured limit")
        with self._task_lock:
            task_id = f"task-{self._next_id}"
            self._next_id += 1
            record = SigRaftTask(
                task_id=task_id,
                action=cleaned,
                state="queued",
                checkpoint=checkpoint,
            )
            self._tasks[task_id] = record
            return record

    def get_task(self, task_id: str) -> SigRaftTask | None:
        """Return a stored task by id."""

        with self._task_lock:
            return self._tasks.get(task_id)

    def list_tasks(self, *, first: int) -> tuple[SigRaftTask, ...]:
        """Return the first tasks in stable submission order."""

        if first < 1 or first > 100:
            raise ValueError("first must be between 1 and 100")
        with self._task_lock:
            return tuple(self._tasks.values())[:first]

    def shutdown(self) -> None:
        """Stop managed reload and telemetry resources."""

        if self.watcher is not None:
            self.watcher.stop()
        if self.analytics is not None:
            self.analytics.stop()
        assert self.telemetry is not None
        self.telemetry.shutdown()


class RequestTooLarge(ValueError):
    """Raised before reading a body above the configured byte limit."""


class InvalidRequest(ValueError):
    """Raised when an HTTP request cannot satisfy the JSON contract."""


def build_handler(service: SigRaftService) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to *service*."""

    class SigRaftHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def _dispatch(self) -> None:
            path = urlparse(self.path).path
            if path in {"/livez", "/readyz"}:
                status, payload = _route_request(service, self.command, self.path, None)
                self._write(status, payload)
                return

            assert service.telemetry is not None
            parent = PROPAGATOR.extract(
                {name.lower(): value for name, value in self.headers.items()}
            )
            route = _route_template(path)
            started = monotonic_ns()
            tracer = service.telemetry.tracer
            with tracer.start_as_current_span(
                f"{self.command} {route}",
                context=parent,
                kind=SpanKind.SERVER,
                attributes={
                    "http.request.method": self.command,
                    "http.route": route,
                },
            ) as span:
                try:
                    status, payload = _route_request(
                        service,
                        self.command,
                        self.path,
                        self._read_body(),
                        scopes=self._scopes(),
                    )
                except JSONDecodeError:
                    status, payload = (
                        HTTPStatus.BAD_REQUEST,
                        {"error": "request body must be valid JSON"},
                    )
                except (InvalidRequest, UnicodeDecodeError):
                    status, payload = (
                        HTTPStatus.BAD_REQUEST,
                        {"error": "request body must be a valid JSON object"},
                    )
                except RequestTooLarge:
                    status, payload = (
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        {"error": "request body exceeds the configured limit"},
                    )
                span.set_attribute("http.response.status_code", int(status))
                if int(status) >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                elapsed_ms = (monotonic_ns() - started) / 1_000_000
                attributes = {
                    "http.request.method": self.command,
                    "http.route": route,
                    "http.response.status_code": int(status),
                }
                service._request_counter.add(1, attributes)
                service._request_duration.record(elapsed_ms, attributes)
                context = set_span_in_context(span, parent)
                emit_request_log(
                    service.telemetry.logger,
                    context=context,
                    route=route,
                    method=self.command,
                    status_code=int(status),
                )
                response_headers: dict[str, str] = {}
                PROPAGATOR.inject(response_headers, context=context)
            self._write(status, payload, response_headers)

        def _read_body(self) -> dict[str, Any] | None:
            if self.command != "POST":
                return None
            assert service.request_limits is not None
            if self.headers.get("Transfer-Encoding") is not None:
                raise InvalidRequest("transfer encoding is not supported")
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise InvalidRequest("content length must be an integer") from error
            if content_length < 0:
                raise InvalidRequest("content length must not be negative")
            if content_length > service.request_limits.current.max_body_bytes:
                raise RequestTooLarge
            raw_body = self.rfile.read(content_length) if content_length else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise InvalidRequest("request body must be a JSON object")
            return payload

        def _scopes(self) -> frozenset[str]:
            raw = self.headers.get("X-SigRaft-Scopes", "")
            return frozenset(scope for scope in raw.split() if scope)

        def _write(
            self,
            status: int,
            payload: dict[str, Any] | str,
            headers: dict[str, str] | None = None,
        ) -> None:
            is_html = isinstance(payload, str)
            encoded = payload.encode("utf-8") if is_html else json.dumps(payload).encode("utf-8")
            self.send_response(int(status))
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8" if is_html else "application/json",
            )
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

    return SigRaftHandler


def _route_request(
    service: SigRaftService,
    method: str,
    raw_path: str,
    body: dict[str, Any] | None,
    *,
    scopes: frozenset[str] = frozenset(),
) -> tuple[int, dict[str, Any] | str]:
    path = urlparse(raw_path).path
    if method == "GET" and path == "/livez":
        return HTTPStatus.OK, {"status": "alive"}
    if method == "GET" and path == "/readyz":
        if service.ready:
            return HTTPStatus.OK, {"status": "ready"}
        return HTTPStatus.SERVICE_UNAVAILABLE, {"status": "starting"}
    if method == "GET" and path == "/metadata":
        return HTTPStatus.OK, service.metadata()
    if method == "POST" and path == "/graphql":
        try:
            return HTTPStatus.OK, execute_http_payload(
                service.graphql,
                body or {},
                scopes=scopes,
            )
        except ValueError as error:
            return HTTPStatus.BAD_REQUEST, {"error": str(error)}
    if method == "POST" and path == "/scheduler/nodes":
        try:
            assert service.scheduler is not None
            service.scheduler.heartbeat(SchedulerNode.from_mapping(body or {}))
        except (TypeError, ValueError) as error:
            return HTTPStatus.BAD_REQUEST, {"error": str(error)}
        return HTTPStatus.ACCEPTED, {"status": "recorded"}
    if method == "POST" and path == "/scheduler/jobs":
        payload = body or {}
        resources = payload.get("resources")
        try:
            if not isinstance(resources, dict):
                raise ValueError("resources must be an object")
            action = payload.get("action")
            project = payload.get("project")
            if not isinstance(action, str) or not isinstance(project, str):
                raise ValueError("action and project must be text")
            priority = _optional_body_integer(payload, "priority", 0)
            max_attempts = _optional_body_integer(payload, "max_attempts", 1)
            if not project.strip() or not 0 <= priority <= 100 or max_attempts < 1:
                raise ValueError("project, priority or max_attempts is invalid")
            resource_request = SchedulerResources.from_mapping(resources)
            assert service.scheduler is not None
            service.scheduler.validate_request(resource_request)
            record = service.submit_task(action)
            job = service.scheduler.submit(
                record.task_id,
                project=project,
                resources=resource_request,
                priority=priority,
                max_attempts=max_attempts,
            )
        except (TypeError, ValueError) as error:
            return HTTPStatus.BAD_REQUEST, {"error": str(error)}
        return HTTPStatus.ACCEPTED, job.as_dict()
    if method == "POST" and path == "/scheduler/run":
        payload = body or {}
        try:
            caller_id = payload.get("caller_id")
            if not isinstance(caller_id, str):
                raise ValueError("caller_id must be text")
            now = _required_body_integer(payload, "now")
            lease_seconds = _optional_body_integer(payload, "lease_seconds", 30)
            assert service.scheduler is not None
            plans = service.scheduler.schedule(
                caller_id=caller_id,
                now=now,
                lease_seconds=lease_seconds,
            )
        except PermissionError as error:
            return HTTPStatus.CONFLICT, {"error": str(error)}
        except (TypeError, ValueError) as error:
            return HTTPStatus.BAD_REQUEST, {"error": str(error)}
        return HTTPStatus.OK, {"dispatches": [plan.as_dict() for plan in plans]}
    if method == "GET" and path.startswith("/scheduler/jobs/") and path.count("/") == 3:
        assert service.scheduler is not None
        task_id = path.rsplit("/", maxsplit=1)[-1]
        job = service.scheduler.job(task_id)
        if job is None:
            return HTTPStatus.NOT_FOUND, {"error": "scheduled job not found"}
        return HTTPStatus.OK, job.as_dict()
    if method == "GET" and path == "/analysis":
        report = service.analytics.report if service.analytics is not None else None
        if report is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "analysis unavailable"}
        return HTTPStatus.OK, report.html
    if method == "GET" and path == "/redfish/v1/":
        assert service.redfish is not None
        return HTTPStatus.OK, service.redfish.service_root()
    if method == "GET" and path == "/redfish/v1/Systems":
        assert service.redfish is not None
        return HTTPStatus.OK, service.redfish.systems_collection()
    if method == "GET" and path.startswith("/redfish/v1/Systems/") and path.count("/") == 4:
        assert service.redfish is not None
        system_id = path.rsplit("/", maxsplit=1)[-1]
        system = service.redfish.system(system_id)
        if system is None:
            return HTTPStatus.NOT_FOUND, {"error": "managed system not found"}
        return HTTPStatus.OK, system
    if method == "POST" and path == "/tasks":
        payload = body if body is not None else {}
        checkpoint_value = payload.get("checkpoint")
        try:
            if checkpoint_value is not None and (
                isinstance(checkpoint_value, bool) or not isinstance(checkpoint_value, int)
            ):
                raise ValueError("checkpoint must be an integer")
            action = payload.get("action", "")
            if not isinstance(action, str):
                raise ValueError("action must be text")
            record = service.submit_task(action, checkpoint_value)
        except (TypeError, ValueError):
            return HTTPStatus.BAD_REQUEST, {"error": "action and checkpoint must be valid"}
        return HTTPStatus.ACCEPTED, asdict(record)
    if method == "GET" and path.startswith("/tasks/") and path.count("/") == 2:
        task_id = path.rsplit("/", maxsplit=1)[-1]
        record = service.get_task(task_id)
        if record is None:
            return HTTPStatus.NOT_FOUND, {"error": "task not found"}
        return HTTPStatus.OK, asdict(record)
    return HTTPStatus.NOT_FOUND, {"error": "not found"}


def _required_body_integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_body_integer(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _route_template(path: str) -> str:
    if path.startswith("/tasks/") and path.count("/") == 2:
        return "/tasks/{task_id}"
    if path.startswith("/redfish/v1/Systems/") and path.count("/") == 4:
        return "/redfish/v1/Systems/{system_id}"
    if path.startswith("/scheduler/jobs/") and path.count("/") == 3:
        return "/scheduler/jobs/{task_id}"
    if path in {
        "/tasks",
        "/metadata",
        "/graphql",
        "/scheduler/nodes",
        "/scheduler/jobs",
        "/scheduler/run",
        "/analysis",
        "/redfish/v1/",
        "/redfish/v1/Systems",
    }:
        return path
    return "unmatched"


@dataclass
class HostedSigRaftServer:
    """A SigRaft server running in a background thread for tests."""

    server: ThreadingHTTPServer
    thread: Thread
    service: SigRaftService

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        """Stop the hosted server and owned service resources."""

        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.service.shutdown()


def run_server(
    service: SigRaftService, host: str = "127.0.0.1", port: int = 0
) -> HostedSigRaftServer:
    """Run *service* in a background thread and return the host handle."""

    server = ThreadingHTTPServer((host, port), build_handler(service))
    thread = Thread(target=server.serve_forever, name="sigraft-server", daemon=True)
    thread.start()
    return HostedSigRaftServer(server=server, thread=thread, service=service)


def create_service(
    *,
    release_digest: str,
    config_path: Path,
    environment: str | None,
    instance_id: str | None,
    watch_config: bool,
    poll_interval: float,
) -> SigRaftService:
    """Create the configured production service and optional file watcher."""

    manager = ConfigManager.from_bytes(config_path.read_bytes())
    settings = manager.snapshot.settings
    telemetry = TelemetryRuntime.from_settings(
        settings.telemetry,
        version=__version__,
        environment=environment or settings.identity.deployment_environment,
        instance_id=instance_id or settings.identity.service_instance_id,
        release_digest=release_digest,
    )
    limits = RequestLimits(settings.requests)
    analytics = AnalyticsRuntime(settings.analysis, base_dir=config_path.parent)
    service = SigRaftService(
        release_digest=release_digest,
        config_manager=manager,
        telemetry=telemetry,
        request_limits=limits,
        analytics=analytics,
    )
    watcher = ConfigWatcher(
        manager,
        config_path,
        interval=poll_interval,
        poll_changes=watch_config,
    )
    service.watcher = watcher
    watcher.start()
    analytics.start()
    return service


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Run the SigRaft service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--environment", default=os.getenv("SIGRAFT_ENVIRONMENT"))
    parser.add_argument("--instance-id", default=os.getenv("SIGRAFT_INSTANCE_ID"))
    parser.add_argument("--poll-interval", default=2.0, type=float)
    parser.add_argument("--watch-config", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)

    service = create_service(
        release_digest=args.digest,
        config_path=args.config,
        environment=args.environment,
        instance_id=args.instance_id,
        watch_config=args.watch_config,
        poll_interval=args.poll_interval,
    )
    watcher = service.watcher
    assert watcher is not None
    previous = signal.signal(signal.SIGHUP, lambda signum, frame: watcher.request_reload())
    hosted = run_server(service, host=args.host, port=args.port)
    try:
        hosted.thread.join()
    finally:
        signal.signal(signal.SIGHUP, previous)
        hosted.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
