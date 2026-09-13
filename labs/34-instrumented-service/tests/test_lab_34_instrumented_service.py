"""Tests for the relay OpenTelemetry checkpoint."""

from __future__ import annotations

import pytest
from opentelemetry import baggage, trace
from opentelemetry.sdk.trace.sampling import Decision, SamplingResult
from opentelemetry.trace import SpanKind, StatusCode

from lab_34_instrumented_service import __version__
from lab_34_instrumented_service.telemetry import (
    ExporterSettings,
    FakeDependency,
    InstrumentedRelayService,
    ManualClock,
    RelayRequest,
    SequentialIdSource,
    TelemetryRuntime,
    context_with_workflow,
    extract_context,
    format_traceparent,
    inject_context,
    load_diagnosis_query,
    parse_traceparent,
    validate_diagnosis_query,
)


def runtime(ids: SequentialIdSource | None = None, sample_ratio: float = 1.0) -> TelemetryRuntime:
    return TelemetryRuntime.in_memory(ids=ids, sample_ratio=sample_ratio)


def metric_values(telemetry: TelemetryRuntime) -> dict[str, list[float]]:
    data = telemetry.collect_metrics()
    return {
        metric.name: [
            point.value if hasattr(point, "value") else point.sum
            for point in metric.data.data_points
        ]
        for resource_metric in data.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }


def metric_attribute_sets(telemetry: TelemetryRuntime) -> list[set[str]]:
    data = telemetry.collect_metrics()
    return [
        set(point.attributes)
        for resource_metric in data.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
        for point in metric.data.data_points
    ]


def test_relay_exports_real_spans_metrics_logs_and_w3c_context() -> None:
    telemetry = runtime(SequentialIdSource(trace_counter=17, span_counter=99))
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)
    dependency = FakeDependency(target="relay-storage", clock=clock, latency_ms=25)
    incoming_trace = format_traceparent("11111111111111111111111111111111", "2222222222222222")

    response = service.handle_request(
        RelayRequest(
            method="POST",
            path="/tasks",
            tenant="tenant-a",
            body={"task": "checkpoint"},
            headers={
                "traceparent": incoming_trace,
                "baggage": "relay.workflow=import",
            },
        ),
        dependency,
    )

    spans = telemetry.finished_spans()
    assert response.status_code == 202
    assert dependency.last_request is not None
    propagated = parse_traceparent(dependency.last_request.headers["traceparent"])
    assert propagated[0] == "11111111111111111111111111111111"
    assert dependency.last_request.headers["baggage"] == "relay.workflow=import"
    assert [span.kind for span in spans] == [SpanKind.CLIENT, SpanKind.SERVER]
    client, server = spans
    assert client.context.trace_id == server.context.trace_id
    assert client.parent is not None
    assert client.parent.span_id == server.context.span_id
    assert server.parent is not None
    assert server.parent.span_id == int("2222222222222222", 16)
    assert server.attributes["relay.workflow"] == "import"
    assert server.resource.attributes["service.name"] == "relay"
    assert metric_values(telemetry) == {
        "relay.requests": [1],
        "relay.request.duration": [27.0],
    }
    assert metric_attribute_sets(telemetry) == [
        {"http.route", "relay.outcome", "relay.dependency"},
        {"http.route", "relay.outcome", "relay.dependency"},
    ]
    log = telemetry.finished_logs()[0]
    assert log.log_record.body == "relay.request"
    assert log.log_record.trace_id == server.context.trace_id
    assert log.log_record.span_id == server.context.span_id
    assert log.resource.attributes["service.name"] == "relay"
    assert "relay.tenant" not in log.log_record.attributes


def test_dependency_failure_sets_sdk_error_status() -> None:
    telemetry = runtime()
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)

    response = service.handle_request(
        RelayRequest("POST", "/tasks", "tenant-b", {"task": "checkpoint"}),
        FakeDependency("relay-storage", clock, fail=True),
    )

    client, server = telemetry.finished_spans()
    assert response.status_code == 502
    assert client.status.status_code is StatusCode.ERROR
    assert client.attributes["error.type"] == "DependencyCallError"
    assert server.status.status_code is StatusCode.ERROR
    log = telemetry.finished_logs()[0].log_record
    assert log.severity_text == "ERROR"
    assert log.trace_id == server.context.trace_id
    assert log.span_id == server.context.span_id
    assert metric_values(telemetry)["relay.requests"] == [1]


def test_health_checks_are_low_noise() -> None:
    telemetry = runtime()
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)

    response = service.handle_request(
        RelayRequest("GET", "/readyz", "ops", {}),
        FakeDependency("relay-storage", clock),
    )

    assert response.status_code == 200
    assert telemetry.finished_spans() == ()
    assert telemetry.collect_metrics() is None
    log = telemetry.finished_logs()[0].log_record
    assert log.trace_id == 0
    assert log.span_id == 0
    assert log.attributes["suppressed"] is True


def test_parent_based_sampling_honors_unsampled_parent() -> None:
    telemetry = runtime()
    clock = ManualClock()
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock)
    unsampled = format_traceparent(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", sampled=False
    )

    service.handle_request(
        RelayRequest("POST", "/tasks", "tenant-a", {}, {"traceparent": unsampled}),
        FakeDependency("relay-storage", clock),
    )

    assert telemetry.finished_spans() == ()
    assert metric_values(telemetry)["relay.requests"] == [1]


def test_sampling_ratio_validation_and_zero_ratio() -> None:
    zero = runtime(sample_ratio=0.0)
    result: SamplingResult = zero.tracer_provider.sampler.should_sample(
        parent_context=None,
        trace_id=1,
        name="root",
    )
    assert result.decision is Decision.DROP
    with pytest.raises(ValueError, match="between 0 and 1"):
        runtime(sample_ratio=1.1)


def test_message_context_and_baggage_round_trip() -> None:
    parent = context_with_workflow("nightly-import")
    carrier: dict[str, str] = {}

    inject_context(parent, carrier)
    restored = extract_context(carrier)

    assert carrier["baggage"] == "relay.workflow=nightly-import"
    assert baggage.get_baggage("relay.workflow", context=restored) == "nightly-import"
    assert trace.get_current_span(restored).get_span_context().is_valid is False
    with pytest.raises(ValueError, match="1 to 64"):
        context_with_workflow("")


def test_exporter_settings_do_not_require_a_live_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "false")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)

    settings = ExporterSettings.from_environment()

    assert settings.otlp_endpoint == "http://collector:4317"
    assert settings.otlp_insecure is False
    assert settings.azure_connection_string is None
    with pytest.raises(ValueError, match="APPLICATIONINSIGHTS_CONNECTION_STRING"):
        TelemetryRuntime.for_azure_monitor(settings)


def test_shutdown_closes_all_three_sdk_providers() -> None:
    telemetry = runtime()

    telemetry.shutdown()

    assert telemetry.finished_spans() == ()
    assert telemetry.finished_logs() == ()


def test_traceparent_helpers_validate_shape() -> None:
    header = format_traceparent(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", sampled=False
    )
    assert parse_traceparent(header) == (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbb",
        False,
    )
    with pytest.raises(ValueError, match="invalid traceparent"):
        parse_traceparent("broken")


def test_kql_artifact_filters_health_noise_and_targets_dependencies() -> None:
    query = load_diagnosis_query()
    assert "AppRequests" in query
    assert "AppDependencies" in query
    assert "/readyz" in query
    validate_diagnosis_query(query)
    with pytest.raises(ValueError, match="missing required fragments"):
        validate_diagnosis_query("AppRequests")


def test_version_is_exposed() -> None:
    assert __version__
