"""Tests for the relay observability checkpoint."""

from __future__ import annotations

import pytest

from lab_29_instrumented_service import __version__
from lab_29_instrumented_service.telemetry import (
    FakeDependency,
    InMemoryTelemetry,
    InstrumentedRelayService,
    ManualClock,
    RelayRequest,
    SequentialIdSource,
    format_traceparent,
    load_diagnosis_query,
    parse_traceparent,
    validate_diagnosis_query,
)


def test_relay_request_exports_spans_metrics_logs_and_trace_context() -> None:
    telemetry = InMemoryTelemetry()
    clock = ManualClock()
    ids = SequentialIdSource(trace_counter=17, span_counter=99)
    service = InstrumentedRelayService(telemetry=telemetry, clock=clock, ids=ids)
    dependency = FakeDependency(target="relay-storage", clock=clock, latency_ms=25)
    incoming_trace = format_traceparent("11111111111111111111111111111111", "2222222222222222")

    response = service.handle_request(
        RelayRequest(
            method="POST",
            path="/tasks",
            tenant="tenant-a",
            body={"task": "checkpoint"},
            headers={"traceparent": incoming_trace},
        ),
        dependency,
    )

    assert response.status_code == 202
    assert response.body["dependency"] == "relay-storage"
    assert len(telemetry.spans) == 2
    assert dependency.last_request is not None
    dependency_trace = parse_traceparent(dependency.last_request.headers["traceparent"])
    assert dependency_trace[0] == "11111111111111111111111111111111"
    assert telemetry.spans[0].kind == "CLIENT"
    assert telemetry.spans[1].kind == "SERVER"
    assert telemetry.spans[1].attributes["relay.tenant"] == "tenant-a"
    assert telemetry.counters[0].name == "relay.requests"
    assert telemetry.histograms[0].value == 27
    assert telemetry.logs[0].attributes["http.status_code"] == 202


def test_dependency_failures_record_error_spans_and_logs() -> None:
    telemetry = InMemoryTelemetry()
    clock = ManualClock()
    service = InstrumentedRelayService(
        telemetry=telemetry,
        clock=clock,
        ids=SequentialIdSource(),
        dependency_target="relay-storage",
    )
    dependency = FakeDependency(target="relay-storage", clock=clock, fail=True)

    response = service.handle_request(
        RelayRequest(method="POST", path="/tasks", tenant="tenant-b", body={"task": "checkpoint"}),
        dependency,
    )

    assert response.status_code == 502
    assert telemetry.spans[0].status == "ERROR"
    assert telemetry.spans[0].error_message == "relay-storage timed out"
    assert telemetry.spans[1].status == "ERROR"
    assert telemetry.logs[0].severity == "ERROR"
    assert telemetry.counters[0].attributes["relay.outcome"] == "error"


def test_health_checks_are_low_noise() -> None:
    telemetry = InMemoryTelemetry()
    service = InstrumentedRelayService(
        telemetry=telemetry,
        clock=ManualClock(),
        ids=SequentialIdSource(),
    )
    dependency = FakeDependency(target="relay-storage", clock=ManualClock())

    response = service.handle_request(
        RelayRequest(method="GET", path="/readyz", tenant="ops", body={}),
        dependency,
    )

    assert response.status_code == 200
    assert telemetry.spans == []
    assert telemetry.counters == []
    assert telemetry.histograms == []
    assert telemetry.logs[0].attributes["suppressed"] is True


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
