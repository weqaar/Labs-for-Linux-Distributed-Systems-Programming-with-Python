"""OpenTelemetry-shaped relay instrumentation with in-memory exporters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_TRACEPARENT_PATTERN = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_HEALTH_ROUTES = {"/livez", "/readyz"}


def lab_root() -> Path:
    """Return the lab root directory."""

    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SpanRecord:
    """A captured trace span."""

    name: str
    kind: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_ns: int
    end_ns: int
    attributes: dict[str, str | int | bool]
    status: str
    error_message: str | None = None


@dataclass(frozen=True)
class MetricPoint:
    """A captured metric point."""

    name: str
    value: int
    attributes: dict[str, str | int | bool]


@dataclass(frozen=True)
class LogRecord:
    """A captured structured log record."""

    severity: str
    body: str
    attributes: dict[str, str | int | bool]
    trace_id: str | None
    span_id: str | None


@dataclass
class InMemoryTelemetry:
    """Collect spans, metrics, and logs for tests."""

    spans: list[SpanRecord] = field(default_factory=list)
    counters: list[MetricPoint] = field(default_factory=list)
    histograms: list[MetricPoint] = field(default_factory=list)
    logs: list[LogRecord] = field(default_factory=list)


@dataclass
class ManualClock:
    """A controllable monotonic clock for deterministic tests."""

    now_value_ns: int = 0

    def now_ns(self) -> int:
        """Return the current monotonic time."""

        return self.now_value_ns

    def advance_ms(self, milliseconds: int) -> None:
        """Advance the clock by whole milliseconds."""

        if milliseconds < 0:
            raise ValueError("milliseconds must not be negative")
        self.now_value_ns += milliseconds * 1_000_000


@dataclass
class SequentialIdSource:
    """Generate deterministic trace and span ids."""

    trace_counter: int = 1
    span_counter: int = 1

    def next_trace_id(self) -> str:
        """Return the next 16-byte trace id in hex."""

        trace_id = f"{self.trace_counter:032x}"
        self.trace_counter += 1
        return trace_id

    def next_span_id(self) -> str:
        """Return the next 8-byte span id in hex."""

        span_id = f"{self.span_counter:016x}"
        self.span_counter += 1
        return span_id


@dataclass(frozen=True)
class RelayRequest:
    """An incoming relay HTTP request."""

    method: str
    path: str
    tenant: str
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RelayResponse:
    """A relay HTTP response."""

    status_code: int
    body: dict[str, Any]
    headers: dict[str, str]


@dataclass(frozen=True)
class DependencyRequest:
    """A dependency request emitted by the relay service."""

    target: str
    body: dict[str, Any]
    headers: dict[str, str]


@dataclass(frozen=True)
class DependencyResponse:
    """A dependency response observed by the relay service."""

    status_code: int
    body: dict[str, Any]


class DependencyTransport(Protocol):
    """A small protocol for fake dependency transports."""

    def call(self, request: DependencyRequest) -> DependencyResponse:
        """Send *request* and return a response."""

        ...


class DependencyCallError(RuntimeError):
    """Raised when the dependency fails."""

    def __init__(self, target: str, message: str) -> None:
        super().__init__(message)
        self.target = target


@dataclass
class FakeDependency:
    """A deterministic dependency transport for tests."""

    target: str
    clock: ManualClock
    latency_ms: int = 20
    fail: bool = False
    last_request: DependencyRequest | None = None

    def call(self, request: DependencyRequest) -> DependencyResponse:
        """Record the request and return or fail after a fixed latency."""

        self.last_request = request
        self.clock.advance_ms(self.latency_ms)
        if self.fail:
            raise DependencyCallError(self.target, f"{self.target} timed out")
        return DependencyResponse(status_code=200, body={"checkpoint": "stored"})


@dataclass
class InstrumentedRelayService:
    """Emit traces, metrics, and structured logs for relay requests."""

    telemetry: InMemoryTelemetry
    clock: ManualClock
    ids: SequentialIdSource
    dependency_target: str = "relay-storage"

    def handle_request(
        self, request: RelayRequest, dependency: DependencyTransport
    ) -> RelayResponse:
        """Handle *request* while exporting telemetry."""

        if request.path in _HEALTH_ROUTES:
            self.telemetry.logs.append(
                LogRecord(
                    severity="DEBUG",
                    body="healthcheck",
                    attributes={"http.route": request.path, "suppressed": True},
                    trace_id=None,
                    span_id=None,
                )
            )
            return RelayResponse(status_code=200, body={"status": "ok"}, headers={})

        trace_id, parent_span_id, sampled = _extract_context(request.headers, self.ids)
        server_span_id = self.ids.next_span_id()
        start_ns = self.clock.now_ns()
        self.clock.advance_ms(1)
        client_span_id = self.ids.next_span_id()
        outbound_traceparent = format_traceparent(trace_id, client_span_id, sampled)
        outcome = "success"
        status_code = 202
        body: dict[str, Any] = {"status": "accepted"}

        dependency_start_ns = self.clock.now_ns()
        try:
            dependency_response = dependency.call(
                DependencyRequest(
                    target=self.dependency_target,
                    body=request.body,
                    headers={"traceparent": outbound_traceparent},
                )
            )
            dependency_end_ns = self.clock.now_ns()
            self.telemetry.spans.append(
                SpanRecord(
                    name="relay.storage",
                    kind="CLIENT",
                    trace_id=trace_id,
                    span_id=client_span_id,
                    parent_span_id=server_span_id,
                    start_ns=dependency_start_ns,
                    end_ns=dependency_end_ns,
                    attributes={
                        "dependency.name": self.dependency_target,
                        "http.status_code": dependency_response.status_code,
                    },
                    status="OK",
                )
            )
            body = {"status": "accepted", "dependency": self.dependency_target}
        except DependencyCallError as error:
            dependency_end_ns = self.clock.now_ns()
            outcome = "error"
            status_code = 502
            body = {"error": str(error), "dependency": error.target}
            self.telemetry.spans.append(
                SpanRecord(
                    name="relay.storage",
                    kind="CLIENT",
                    trace_id=trace_id,
                    span_id=client_span_id,
                    parent_span_id=server_span_id,
                    start_ns=dependency_start_ns,
                    end_ns=dependency_end_ns,
                    attributes={
                        "dependency.name": error.target,
                        "error.type": "DependencyCallError",
                    },
                    status="ERROR",
                    error_message=str(error),
                )
            )
        self.clock.advance_ms(1)
        end_ns = self.clock.now_ns()
        metric_attributes: dict[str, str | int | bool] = {
            "http.method": request.method,
            "http.route": request.path,
            "relay.outcome": outcome,
            "relay.tenant": request.tenant,
            "relay.dependency": self.dependency_target,
        }
        self.telemetry.spans.append(
            SpanRecord(
                name="relay.request",
                kind="SERVER",
                trace_id=trace_id,
                span_id=server_span_id,
                parent_span_id=parent_span_id,
                start_ns=start_ns,
                end_ns=end_ns,
                attributes={
                    **metric_attributes,
                    "http.status_code": status_code,
                },
                status="OK" if status_code < 500 else "ERROR",
                error_message=None if status_code < 500 else body["error"],
            )
        )
        self.telemetry.counters.append(MetricPoint("relay.requests", 1, metric_attributes))
        self.telemetry.histograms.append(
            MetricPoint(
                "relay.request.duration_ms",
                int((end_ns - start_ns) / 1_000_000),
                metric_attributes,
            )
        )
        self.telemetry.logs.append(
            LogRecord(
                severity="INFO" if status_code < 500 else "ERROR",
                body="relay.request",
                attributes={
                    **metric_attributes,
                    "http.status_code": status_code,
                },
                trace_id=trace_id,
                span_id=server_span_id,
            )
        )
        return RelayResponse(
            status_code=status_code,
            body=body,
            headers={"traceparent": format_traceparent(trace_id, server_span_id, sampled)},
        )


def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    """Format a W3C traceparent header."""

    if len(trace_id) != 32 or len(span_id) != 16:
        raise ValueError("trace_id must be 32 hex chars and span_id must be 16 hex chars")
    flags = "01" if sampled else "00"
    return f"00-{trace_id}-{span_id}-{flags}"


def parse_traceparent(value: str) -> tuple[str, str, bool]:
    """Parse a W3C traceparent header."""

    match = _TRACEPARENT_PATTERN.match(value)
    if match is None:
        raise ValueError("invalid traceparent header")
    trace_id, span_id, flags = match.groups()
    return trace_id, span_id, int(flags, 16) & 1 == 1


def load_diagnosis_query(root: Path | None = None) -> str:
    """Load the representative KQL diagnosis query."""

    actual_root = root if root is not None else lab_root()
    query = (actual_root / "artifacts/relay_diagnosis.kql").read_text(encoding="utf-8")
    validate_diagnosis_query(query)
    return query


def validate_diagnosis_query(query: str) -> None:
    """Check that the KQL artifact can identify a bad dependency."""

    required_fragments = [
        "AppRequests",
        "AppDependencies",
        "join kind=leftouter",
        "OperationId",
        "dependency_failures",
        "/livez",
        "/readyz",
        "summarize",
        "Target",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in query]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"diagnosis query is missing required fragments: {joined}")


def _extract_context(
    headers: dict[str, str], ids: SequentialIdSource
) -> tuple[str, str | None, bool]:
    traceparent = headers.get("traceparent")
    if traceparent is None:
        return ids.next_trace_id(), None, True
    trace_id, parent_span_id, sampled = parse_traceparent(traceparent)
    return trace_id, parent_span_id, sampled
