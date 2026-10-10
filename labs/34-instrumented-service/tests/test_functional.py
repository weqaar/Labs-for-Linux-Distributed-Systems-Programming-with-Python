"""Functional tests for the instrumented relay request handler.

These tests drive the composed service the way the lab uses it: an
`InstrumentedRelayService` built on `TelemetryRuntime.in_memory()` handles
whole `POST /tasks` requests through `handle_request`. Each test then reads
the spans, metrics and logs that the real OpenTelemetry SDK exported to its
in-memory exporters. No collector, network or Azure account is involved.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

import pytest
from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    MetricsData,
    NumberDataPoint,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanKind, StatusCode

from lab_34_instrumented_service import (
    FakeDependency,
    InstrumentedRelayService,
    ManualClock,
    RelayRequest,
    SequentialIdSource,
    TelemetryRuntime,
    context_with_workflow,
    format_traceparent,
    inject_context,
    parse_traceparent,
)

MetricPoint = tuple[str, dict[str, object], float]


class ExportedLogRecord(Protocol):
    """The fields of an SDK log record that these tests read."""

    @property
    def body(self) -> object: ...
    @property
    def severity_text(self) -> str | None: ...
    @property
    def trace_id(self) -> int | None: ...
    @property
    def span_id(self) -> int | None: ...


class ExportedLog(Protocol):
    """An exported SDK log entry; its class name changed across SDK releases."""

    @property
    def log_record(self) -> ExportedLogRecord: ...


@pytest.fixture
def telemetry() -> Iterator[TelemetryRuntime]:
    runtime = TelemetryRuntime.in_memory(ids=SequentialIdSource(trace_counter=17, span_counter=1))
    yield runtime
    runtime.shutdown()


def spans_of(telemetry: TelemetryRuntime) -> tuple[ReadableSpan, ...]:
    spans: tuple[ReadableSpan, ...] = telemetry.finished_spans()
    return spans


def logs_of(telemetry: TelemetryRuntime) -> tuple[ExportedLog, ...]:
    logs: tuple[ExportedLog, ...] = telemetry.finished_logs()
    return logs


def metric_points(telemetry: TelemetryRuntime) -> list[MetricPoint]:
    data: MetricsData | None = telemetry.collect_metrics()
    if data is None:
        return []
    points: list[MetricPoint] = []
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                for point in metric.data.data_points:
                    attributes: dict[str, object] = dict(point.attributes or {})
                    if isinstance(point, HistogramDataPoint):
                        points.append((metric.name, attributes, float(point.sum)))
                    elif isinstance(point, NumberDataPoint):
                        points.append((metric.name, attributes, float(point.value)))
    return sorted(points, key=lambda item: (item[0], str(sorted(item[1].items()))))


def submit(
    service: InstrumentedRelayService,
    dependency: FakeDependency,
    task_id: str,
    headers: dict[str, str] | None = None,
) -> int:
    response = service.handle_request(
        RelayRequest(
            method="POST",
            path="/tasks",
            tenant="tenant-a",
            body={"id": task_id, "action": "checkpoint"},
            headers=headers or {},
        ),
        dependency,
    )
    return response.status_code


def test_accepted_task_request_produces_one_correlated_trace_metric_and_log(
    telemetry: TelemetryRuntime,
) -> None:
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)
    dependency = FakeDependency("relay-storage", clock, latency_ms=40)

    status = submit(service, dependency, "task-17")

    assert status == 202
    client, server = spans_of(telemetry)
    assert (server.name, server.kind) == ("POST /tasks", SpanKind.SERVER)
    assert (client.name, client.kind) == ("relay.storage", SpanKind.CLIENT)
    assert client.parent is not None and server.context is not None
    assert client.parent.span_id == server.context.span_id
    assert dependency.last_request is not None
    sent_trace_id, _, sampled = parse_traceparent(dependency.last_request.headers["traceparent"])
    assert int(sent_trace_id, 16) == server.context.trace_id
    assert sampled is True
    (log,) = logs_of(telemetry)
    assert log.log_record.body == "relay.request"
    assert log.log_record.trace_id == server.context.trace_id
    assert log.log_record.span_id == server.context.span_id
    expected = {
        "http.route": "/tasks",
        "relay.outcome": "success",
        "relay.dependency": "relay-storage",
    }
    assert metric_points(telemetry) == [
        ("relay.request.duration", expected, 42.0),
        ("relay.requests", expected, 1.0),
    ]


def test_task_ids_never_become_metric_labels_across_many_requests(
    telemetry: TelemetryRuntime,
) -> None:
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)
    dependency = FakeDependency("relay-storage", clock, latency_ms=10)

    statuses = [submit(service, dependency, f"task-{number}") for number in range(17, 22)]

    assert statuses == [202] * 5
    assert len(spans_of(telemetry)) == 10
    points = metric_points(telemetry)
    assert [(name, value) for name, _, value in points] == [
        ("relay.request.duration", 60.0),
        ("relay.requests", 5.0),
    ]
    for _, attributes, _ in points:
        assert not any("task-" in str(value) for value in attributes.values())


def test_dependency_timeout_returns_502_and_marks_every_signal_as_failed(
    telemetry: TelemetryRuntime,
) -> None:
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)
    dependency = FakeDependency("relay-storage", clock, fail=True)

    response = service.handle_request(
        RelayRequest("POST", "/tasks", "tenant-a", {"id": "task-17", "action": "checkpoint"}),
        dependency,
    )

    assert response.status_code == 502
    assert response.body == {"error": "relay-storage timed out", "dependency": "relay-storage"}
    client, server = spans_of(telemetry)
    assert client.status.status_code is StatusCode.ERROR
    assert server.status.status_code is StatusCode.ERROR
    (log,) = logs_of(telemetry)
    assert log.log_record.severity_text == "ERROR"
    assert server.context is not None
    assert log.log_record.trace_id == server.context.trace_id
    outcomes = {attributes["relay.outcome"] for _, attributes, _ in metric_points(telemetry)}
    assert outcomes == {"error"}


def test_queued_message_context_continues_the_producer_trace_and_workflow(
    telemetry: TelemetryRuntime,
) -> None:
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)
    dependency = FakeDependency("relay-storage", clock)
    producer_trace = "0000000000000000000000000000abcd"
    message_properties: dict[str, str] = {
        "traceparent": format_traceparent(producer_trace, "00000000000000ef"),
    }
    inject_context(context_with_workflow("nightly-import"), message_properties)

    status = submit(service, dependency, "task-17", headers=message_properties)

    assert status == 202
    _, server = spans_of(telemetry)
    assert server.context is not None and server.parent is not None
    assert server.context.trace_id == int(producer_trace, 16)
    assert server.parent.span_id == int("ef", 16)
    assert server.attributes is not None
    assert server.attributes["relay.workflow"] == "nightly-import"
    assert dependency.last_request is not None
    assert dependency.last_request.headers["baggage"] == "relay.workflow=nightly-import"
