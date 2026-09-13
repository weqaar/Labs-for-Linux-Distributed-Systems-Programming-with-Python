"""Tests for the runnable service OpenTelemetry signals."""

from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from opentelemetry.trace import SpanKind

from lab_39_sigraft_service.sigraft_service import SigRaftService, run_server
from lab_39_sigraft_service.telemetry import TelemetryRuntime
from lab_39_sigraft_service.version import __version__

DIGEST = "sha256:" + "b" * 64


def request(url: str, *, traceparent: str | None = None) -> int:
    headers = {"Content-Type": "application/json"}
    if traceparent is not None:
        headers["traceparent"] = traceparent
    operation = Request(
        url,
        data=json.dumps({"action": "observe"}).encode(),
        headers=headers,
        method="POST",
    )
    with urlopen(operation) as response:
        return response.status


def metric_attributes(telemetry: TelemetryRuntime) -> list[set[str]]:
    data = telemetry.collect_metrics()
    return [
        set(point.attributes)
        for resource_metric in data.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
        for point in metric.data.data_points
    ]


def test_http_request_emits_correlated_sdk_signals_and_resource() -> None:
    telemetry = TelemetryRuntime.in_memory(
        version=__version__,
        environment="integration",
        instance_id="sigraft-7",
        release_digest=DIGEST,
    )
    hosted = run_server(SigRaftService(DIGEST, telemetry=telemetry))
    parent_trace_id = "1" * 32
    parent_span_id = "2" * 16
    try:
        assert (
            request(
                f"{hosted.base_url}/tasks",
                traceparent=f"00-{parent_trace_id}-{parent_span_id}-01",
            )
            == 202
        )

        span = telemetry.finished_spans()[0]
        log = telemetry.finished_logs()[0]
        assert span.kind is SpanKind.SERVER
        assert span.context.trace_id == int(parent_trace_id, 16)
        assert span.parent is not None
        assert span.parent.span_id == int(parent_span_id, 16)
        assert log.log_record.trace_id == span.context.trace_id
        assert log.log_record.span_id == span.context.span_id
        assert log.log_record.body == "sigraft.request"
        assert metric_attributes(telemetry) == [
            {"http.request.method", "http.route", "http.response.status_code"},
            {"http.request.method", "http.route", "http.response.status_code"},
        ]
        resource = span.resource.attributes
        assert resource["service.name"] == "sigraft"
        assert resource["service.version"] == __version__
        assert resource["deployment.environment.name"] == "integration"
        assert resource["service.instance.id"] == "sigraft-7"
        assert resource["service.release.digest"] == DIGEST
    finally:
        hosted.close()


def test_health_routes_emit_no_application_telemetry() -> None:
    telemetry = TelemetryRuntime.in_memory(
        version=__version__,
        environment="test",
        instance_id="health-test",
        release_digest=DIGEST,
    )
    hosted = run_server(SigRaftService(DIGEST, telemetry=telemetry))
    try:
        with urlopen(f"{hosted.base_url}/readyz") as response:
            assert response.status == 200

        assert telemetry.finished_spans() == ()
        assert telemetry.finished_logs() == ()
        assert telemetry.collect_metrics() is None
    finally:
        hosted.close()


def test_unknown_paths_share_one_bounded_route_dimension() -> None:
    telemetry = TelemetryRuntime.in_memory(
        version=__version__,
        environment="test",
        instance_id="route-test",
        release_digest=DIGEST,
    )
    hosted = run_server(SigRaftService(DIGEST, telemetry=telemetry))
    try:
        for path in ("/unknown/one", "/unknown/two"):
            try:
                urlopen(f"{hosted.base_url}{path}")
            except HTTPError as error:
                assert error.code == 404

        spans = telemetry.finished_spans()
        assert [span.attributes["http.route"] for span in spans] == [
            "unmatched",
            "unmatched",
        ]
        data = telemetry.collect_metrics()
        routes = {
            point.attributes["http.route"]
            for resource_metric in data.resource_metrics
            for scope_metric in resource_metric.scope_metrics
            for metric in scope_metric.metrics
            for point in metric.data.data_points
        }
        assert routes == {"unmatched"}
    finally:
        hosted.close()
