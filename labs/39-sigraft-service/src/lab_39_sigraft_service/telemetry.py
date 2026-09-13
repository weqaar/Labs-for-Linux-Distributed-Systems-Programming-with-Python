"""OpenTelemetry providers and live sampling for the SigRaft service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from threading import RLock
from typing import Any

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
from opentelemetry.sdk.trace.sampling import (
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind, Tracer, TraceState
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from lab_39_sigraft_service.config import (
    PreparedChange,
    ServiceSettings,
    TelemetrySettings,
)

PROPAGATOR = CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


class ReloadableSampler(Sampler):
    """Apply a thread-safe head-sampling ratio to new root traces."""

    name = "telemetry-sampler"

    def __init__(self, ratio: float) -> None:
        self._lock = RLock()
        self._ratio = ratio
        self._sampler = ParentBased(TraceIdRatioBased(ratio))

    @property
    def ratio(self) -> float:
        """Return the ratio used for subsequent sampling decisions."""

        with self._lock:
            return self._ratio

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: dict[str, Any] | None = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        """Delegate one decision to the current immutable sampler."""

        with self._lock:
            sampler = self._sampler
        return sampler.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        """Describe the current root ratio."""

        return f"ReloadableParentBased{{{self.ratio:g}}}"

    def prepare(self, candidate: ServiceSettings, current: ServiceSettings) -> PreparedChange:
        del current
        return _SamplerChange(self, self.ratio, candidate.telemetry.sample_ratio)

    def _set_ratio(self, ratio: float) -> None:
        with self._lock:
            self._ratio = ratio
            self._sampler = ParentBased(TraceIdRatioBased(ratio))


@dataclass
class _SamplerChange:
    sampler: ReloadableSampler
    old: float
    new: float

    def commit(self) -> None:
        self.sampler._set_ratio(self.new)

    def rollback(self) -> None:
        self.sampler._set_ratio(self.old)


@dataclass
class TelemetryRuntime:
    """Own all three OpenTelemetry SDK signal providers."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    resource: Resource
    sampler: ReloadableSampler
    span_exporter: InMemorySpanExporter | None = None
    metric_reader: InMemoryMetricReader | None = None
    log_exporter: InMemoryLogRecordExporter | None = None

    @classmethod
    def in_memory(
        cls,
        *,
        version: str,
        environment: str,
        instance_id: str,
        release_digest: str,
        sample_ratio: float = 1.0,
    ) -> TelemetryRuntime:
        """Create deterministic SDK exporters and readers with no network I/O."""

        resource = _resource(version, environment, instance_id, release_digest)
        sampler = ReloadableSampler(sample_ratio)
        spans = InMemorySpanExporter()
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)
        tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
        metrics = InMemoryMetricReader()
        meter_provider = MeterProvider(resource=resource, metric_readers=[metrics])
        logs = InMemoryLogRecordExporter()
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
        return cls(
            tracer_provider,
            meter_provider,
            logger_provider,
            resource,
            sampler,
            spans,
            metrics,
            logs,
        )

    @classmethod
    def from_settings(
        cls,
        settings: TelemetrySettings,
        *,
        version: str,
        environment: str,
        instance_id: str,
        release_digest: str,
    ) -> TelemetryRuntime:
        """Select the configured startup exporter."""

        if settings.exporter == "memory":
            return cls.in_memory(
                version=version,
                environment=environment,
                instance_id=instance_id,
                release_digest=release_digest,
                sample_ratio=settings.sample_ratio,
            )
        if settings.exporter == "otlp":
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            return cls._exporting(
                settings,
                version,
                environment,
                instance_id,
                release_digest,
                OTLPSpanExporter(
                    endpoint=settings.otlp_endpoint,
                    insecure=settings.otlp_insecure,
                ),
                OTLPMetricExporter(
                    endpoint=settings.otlp_endpoint,
                    insecure=settings.otlp_insecure,
                ),
                OTLPLogExporter(
                    endpoint=settings.otlp_endpoint,
                    insecure=settings.otlp_insecure,
                ),
            )
        try:
            azure = import_module("azure.monitor.opentelemetry.exporter")
        except ModuleNotFoundError as error:
            raise RuntimeError("install the 'azure' extra to export to Azure Monitor") from error
        connection_string = settings.azure_connection_string
        return cls._exporting(
            settings,
            version,
            environment,
            instance_id,
            release_digest,
            azure.AzureMonitorTraceExporter(connection_string=connection_string),
            azure.AzureMonitorMetricExporter(connection_string=connection_string),
            azure.AzureMonitorLogExporter(connection_string=connection_string),
        )

    @classmethod
    def _exporting(
        cls,
        settings: TelemetrySettings,
        version: str,
        environment: str,
        instance_id: str,
        release_digest: str,
        span_exporter: SpanExporter,
        metric_exporter: Any,
        log_exporter: LogRecordExporter,
    ) -> TelemetryRuntime:
        resource = _resource(version, environment, instance_id, release_digest)
        sampler = ReloadableSampler(settings.sample_ratio)
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        metric_reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        return cls(tracer_provider, meter_provider, logger_provider, resource, sampler)

    @property
    def tracer(self) -> Tracer:
        return self.tracer_provider.get_tracer("sigraft.http")

    @property
    def meter(self) -> Meter:
        return self.meter_provider.get_meter("sigraft.http")

    @property
    def logger(self) -> Logger:
        return self.logger_provider.get_logger("sigraft.http")

    def finished_spans(self) -> tuple[Any, ...]:
        if self.span_exporter is None:
            raise RuntimeError("finished spans require the in-memory exporter")
        return self.span_exporter.get_finished_spans()

    def collect_metrics(self) -> Any:
        if self.metric_reader is None:
            raise RuntimeError("metrics data requires the in-memory reader")
        return self.metric_reader.get_metrics_data()

    def finished_logs(self) -> tuple[Any, ...]:
        if self.log_exporter is None:
            raise RuntimeError("finished logs require the in-memory exporter")
        return self.log_exporter.get_finished_logs()

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        self.logger_provider.shutdown()


def emit_request_log(
    logger: Logger,
    *,
    context: Context,
    route: str,
    method: str,
    status_code: int,
) -> None:
    """Emit one bounded request event correlated through SDK context."""

    logger.emit(
        context=context,
        severity_number=(SeverityNumber.INFO if status_code < 500 else SeverityNumber.ERROR),
        severity_text="INFO" if status_code < 500 else "ERROR",
        body="sigraft.request",
        attributes={
            "http.request.method": method,
            "http.route": route,
            "http.response.status_code": status_code,
        },
    )


def _resource(version: str, environment: str, instance_id: str, release_digest: str) -> Resource:
    return Resource.create(
        {
            "service.name": "sigraft",
            "service.version": version,
            "deployment.environment.name": environment,
            "service.instance.id": instance_id,
            "service.release.digest": release_digest,
        }
    )
