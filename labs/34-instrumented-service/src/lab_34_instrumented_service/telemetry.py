"""OpenTelemetry instrumentation and exporter configuration for relay."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from opentelemetry import baggage
from opentelemetry._logs import Logger, SeverityNumber
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.metrics import Meter
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    InMemoryLogRecordExporter,
    LogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer, set_span_in_context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_TRACEPARENT_PATTERN = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_HEALTH_ROUTES = {"/livez", "/readyz"}
_PROPAGATOR = CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


def lab_root() -> Path:
    """Return the lab root directory."""

    return Path(__file__).resolve().parents[2]


@dataclass
class ManualClock:
    """A controllable monotonic clock for deterministic duration attributes."""

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
class SequentialIdSource(IdGenerator):
    """Generate deterministic valid OpenTelemetry trace and span identifiers."""

    trace_counter: int = 1
    span_counter: int = 1

    def generate_trace_id(self) -> int:
        """Return the next trace identifier."""

        value = self.trace_counter
        self.trace_counter += 1
        return value

    def generate_span_id(self) -> int:
        """Return the next span identifier."""

        value = self.span_counter
        self.span_counter += 1
        return value

    def next_trace_id(self) -> str:
        """Return a trace identifier in the form used by W3C headers."""

        return f"{self.generate_trace_id():032x}"

    def next_span_id(self) -> str:
        """Return a span identifier in the form used by W3C headers."""

        return f"{self.generate_span_id():016x}"


@dataclass(frozen=True)
class ExporterSettings:
    """Deployment settings for collector and Azure Monitor adapters."""

    otlp_endpoint: str = "http://localhost:4317"
    otlp_insecure: bool = True
    azure_connection_string: str | None = None

    @classmethod
    def from_environment(cls) -> ExporterSettings:
        """Read exporter destinations without creating network clients."""

        insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"
        return cls(
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            otlp_insecure=insecure,
            azure_connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"),
        )


@dataclass
class TelemetryRuntime:
    """Own OpenTelemetry providers and their deterministic test exporters."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    resource: Resource
    span_reader: InMemorySpanExporter | None = None
    metric_reader: InMemoryMetricReader | None = None
    log_reader: InMemoryLogRecordExporter | None = None

    @classmethod
    def in_memory(
        cls,
        *,
        service_name: str = "relay",
        service_version: str = "0.1.0",
        sample_ratio: float = 1.0,
        ids: IdGenerator | None = None,
    ) -> TelemetryRuntime:
        """Build real SDK providers with readers that perform no I/O."""

        resource = _resource(service_name, service_version)
        spans = InMemorySpanExporter()
        tracer_provider = TracerProvider(
            resource=resource,
            sampler=_sampler(sample_ratio),
            id_generator=ids,
        )
        tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
        metrics = InMemoryMetricReader()
        meter_provider = MeterProvider(resource=resource, metric_readers=[metrics])
        logs = InMemoryLogRecordExporter()
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
        return cls(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            logger_provider=logger_provider,
            resource=resource,
            span_reader=spans,
            metric_reader=metrics,
            log_reader=logs,
        )

    @classmethod
    def for_otlp(
        cls,
        settings: ExporterSettings,
        *,
        service_name: str = "relay",
        service_version: str = "0.1.0",
        sample_ratio: float = 0.1,
    ) -> TelemetryRuntime:
        """Build providers that export OTLP over gRPC to an ingestion collector."""

        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        span_exporter = OTLPSpanExporter(
            endpoint=settings.otlp_endpoint, insecure=settings.otlp_insecure
        )
        metric_exporter = OTLPMetricExporter(
            endpoint=settings.otlp_endpoint, insecure=settings.otlp_insecure
        )
        log_exporter = OTLPLogExporter(
            endpoint=settings.otlp_endpoint, insecure=settings.otlp_insecure
        )
        return cls._exporting(
            span_exporter,
            PeriodicExportingMetricReader(metric_exporter),
            log_exporter,
            service_name,
            service_version,
            sample_ratio,
        )

    @classmethod
    def for_azure_monitor(
        cls,
        settings: ExporterSettings,
        *,
        service_name: str = "relay",
        service_version: str = "0.1.0",
        sample_ratio: float = 0.1,
    ) -> TelemetryRuntime:
        """Build the optional Application Insights exporter adapter."""

        if not settings.azure_connection_string:
            raise ValueError("APPLICATIONINSIGHTS_CONNECTION_STRING is required")
        try:
            azure_exporter = import_module("azure.monitor.opentelemetry.exporter")
        except ModuleNotFoundError as error:
            raise RuntimeError("install the 'azure' extra to export to Azure Monitor") from error

        span_exporter = azure_exporter.AzureMonitorTraceExporter(
            connection_string=settings.azure_connection_string
        )
        metric_exporter = azure_exporter.AzureMonitorMetricExporter(
            connection_string=settings.azure_connection_string
        )
        log_exporter = azure_exporter.AzureMonitorLogExporter(
            connection_string=settings.azure_connection_string
        )
        return cls._exporting(
            span_exporter,
            PeriodicExportingMetricReader(metric_exporter),
            log_exporter,
            service_name,
            service_version,
            sample_ratio,
        )

    @classmethod
    def _exporting(
        cls,
        span_exporter: SpanExporter,
        metric_reader: PeriodicExportingMetricReader,
        log_exporter: LogRecordExporter,
        service_name: str,
        service_version: str,
        sample_ratio: float,
    ) -> TelemetryRuntime:
        resource = _resource(service_name, service_version)
        tracer_provider = TracerProvider(resource=resource, sampler=_sampler(sample_ratio))
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        return cls(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            logger_provider=logger_provider,
            resource=resource,
        )

    @property
    def tracer(self) -> Tracer:
        """Return relay's tracer from this runtime, without global state."""

        return self.tracer_provider.get_tracer("relay.service")

    @property
    def meter(self) -> Meter:
        """Return relay's meter from this runtime, without global state."""

        return self.meter_provider.get_meter("relay.service")

    @property
    def logger(self) -> Logger:
        """Return relay's logger from this runtime, without global state."""

        return self.logger_provider.get_logger("relay.service")

    def finished_spans(self) -> tuple[Any, ...]:
        """Return SDK ReadableSpan values when using the in-memory adapter."""

        if self.span_reader is None:
            raise RuntimeError("finished spans are available only in memory")
        return self.span_reader.get_finished_spans()

    def collect_metrics(self) -> Any:
        """Collect SDK MetricsData when using the in-memory adapter."""

        if self.metric_reader is None:
            raise RuntimeError("metric data is available only in memory")
        return self.metric_reader.get_metrics_data()

    def finished_logs(self) -> tuple[Any, ...]:
        """Return SDK ReadableLogRecord values when using the in-memory adapter."""

        if self.log_reader is None:
            raise RuntimeError("finished logs are available only in memory")
        return self.log_reader.get_finished_logs()

    def shutdown(self) -> None:
        """Flush and close trace, metric, and log providers."""

        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        self.logger_provider.shutdown()


# The old teaching name remains as a compatibility alias.
InMemoryTelemetry = TelemetryRuntime


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
    """Create real spans and metric instruments around relay requests."""

    telemetry: TelemetryRuntime
    clock: ManualClock
    ids: SequentialIdSource | None = None
    dependency_target: str = "relay-storage"
    _tracer: Tracer = field(init=False)
    _logger: Logger = field(init=False)
    _requests: Any = field(init=False)
    _duration: Any = field(init=False)

    def __post_init__(self) -> None:
        self._tracer = self.telemetry.tracer
        self._logger = self.telemetry.logger
        meter = self.telemetry.meter
        self._requests = meter.create_counter(
            "relay.requests", unit="{request}", description="Accepted relay requests"
        )
        self._duration = meter.create_histogram(
            "relay.request.duration", unit="ms", description="Relay request duration"
        )

    def handle_request(
        self, request: RelayRequest, dependency: DependencyTransport
    ) -> RelayResponse:
        """Handle a request while recording OpenTelemetry signals."""

        if request.path in _HEALTH_ROUTES:
            self._logger.emit(
                severity_number=SeverityNumber.DEBUG,
                severity_text="DEBUG",
                body="healthcheck",
                attributes={"http.route": request.path, "suppressed": True},
            )
            return RelayResponse(200, {"status": "ok"}, {})

        parent_context = _PROPAGATOR.extract(request.headers)
        started_ns = self.clock.now_ns()
        with self._tracer.start_as_current_span(
            "POST /tasks", context=parent_context, kind=SpanKind.SERVER
        ) as server_span:
            self.clock.advance_ms(1)
            outcome = "success"
            status_code = 202
            body: dict[str, Any] = {"status": "accepted"}
            with self._tracer.start_as_current_span(
                "relay.storage", kind=SpanKind.CLIENT
            ) as client_span:
                carrier: dict[str, str] = {}
                _PROPAGATOR.inject(
                    carrier, context=set_span_in_context(client_span, parent_context)
                )
                try:
                    dependency_response = dependency.call(
                        DependencyRequest(self.dependency_target, request.body, carrier)
                    )
                    client_span.set_attribute(
                        "http.response.status_code", dependency_response.status_code
                    )
                except DependencyCallError as error:
                    outcome = "error"
                    status_code = 502
                    body = {"error": str(error), "dependency": error.target}
                    client_span.set_attribute("error.type", type(error).__name__)
                    client_span.set_status(Status(StatusCode.ERROR, str(error)))
                client_span.set_attribute("server.address", self.dependency_target)

            self.clock.advance_ms(1)
            duration_ms = (self.clock.now_ns() - started_ns) / 1_000_000
            metric_attributes: dict[str, str | int | bool] = {
                "http.route": request.path,
                "relay.outcome": outcome,
                "relay.dependency": self.dependency_target,
            }
            span_attributes: dict[str, str | int | bool] = {
                "http.request.method": request.method,
                **metric_attributes,
                "relay.tenant": request.tenant,
            }
            workflow = baggage.get_baggage("relay.workflow", context=parent_context)
            if isinstance(workflow, str):
                server_span.set_attribute("relay.workflow", workflow)
            server_span.set_attributes(
                {**span_attributes, "http.response.status_code": status_code}
            )
            if status_code >= 500:
                server_span.set_status(Status(StatusCode.ERROR, "dependency request failed"))
            self._requests.add(1, metric_attributes)
            self._duration.record(duration_ms, metric_attributes)
            self._logger.emit(
                context=set_span_in_context(server_span, parent_context),
                severity_number=(
                    SeverityNumber.INFO if status_code < 500 else SeverityNumber.ERROR
                ),
                severity_text="INFO" if status_code < 500 else "ERROR",
                body="relay.request",
                attributes={
                    **metric_attributes,
                    "http.response.status_code": status_code,
                },
            )
            response_carrier: dict[str, str] = {}
            _PROPAGATOR.inject(
                response_carrier, context=set_span_in_context(server_span, parent_context)
            )
            if status_code < 500:
                body = {"status": "accepted", "dependency": self.dependency_target}
            return RelayResponse(status_code, body, response_carrier)


def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    """Format a W3C traceparent header for examples and tests."""

    if len(trace_id) != 32 or len(span_id) != 16:
        raise ValueError("trace_id must be 32 hex chars and span_id must be 16 hex chars")
    flags = "01" if sampled else "00"
    return f"00-{trace_id}-{span_id}-{flags}"


def parse_traceparent(value: str) -> tuple[str, str, bool]:
    """Parse a W3C traceparent header for examples and tests."""

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
        raise ValueError(f"diagnosis query is missing required fragments: {', '.join(missing)}")


def context_with_workflow(workflow: str, context: Context | None = None) -> Context:
    """Return context carrying one allowlisted workflow baggage value."""

    if not workflow or len(workflow) > 64:
        raise ValueError("workflow baggage must contain 1 to 64 characters")
    return baggage.set_baggage("relay.workflow", workflow, context=context)


def inject_context(context: Context, carrier: dict[str, str]) -> None:
    """Inject W3C trace context and baggage into a message carrier."""

    _PROPAGATOR.inject(carrier, context=context)


def extract_context(carrier: dict[str, str]) -> Context:
    """Extract W3C trace context and baggage from a message carrier."""

    return _PROPAGATOR.extract(carrier)


def _resource(service_name: str, service_version: str) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment.name": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
        }
    )


def _sampler(sample_ratio: float) -> ParentBased:
    if not 0.0 <= sample_ratio <= 1.0:
        raise ValueError("sample_ratio must be between 0 and 1")
    root = ALWAYS_ON if sample_ratio == 1.0 else TraceIdRatioBased(sample_ratio)
    return ParentBased(root)
