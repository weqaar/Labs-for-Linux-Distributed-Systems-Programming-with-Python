# Lab 34 Instrumented Service

This checkpoint gives relay real OpenTelemetry Python instrumentation. It
creates SDK `TracerProvider`, `MeterProvider`, and `LoggerProvider` instances,
identifies relay with a `Resource`, records server and client spans, emits
correlated log records, propagates W3C context and baggage, and measures
request count and duration. Tests use the SDK's in-memory span and log
exporters and metric reader. They do not need a collector, Azure account,
network, or credentials.

## Install and run the gates

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

The optional direct Azure adapter has a separate extra:

```bash
pip install -e ".[azure]"
```

## Follow one request

`TelemetryRuntime.in_memory()` creates a resource, providers, an
`InMemorySpanExporter`, an `InMemoryMetricReader`, a `LoggerProvider`, and an
`InMemoryLogRecordExporter`. The relay handler then:

1. extracts `traceparent` and `baggage` with OpenTelemetry propagators;
2. starts a `SpanKind.SERVER` span named `POST /tasks`;
3. starts a child `SpanKind.CLIENT` span for `relay.storage`;
4. injects the client context into the dependency request;
5. adds one `relay.requests` counter measurement;
6. records milliseconds in the `relay.request.duration` histogram; and
7. emits an SDK log record correlated with the server trace and span IDs.

The manual clock controls the measured duration. `SequentialIdSource` is an
SDK `IdGenerator`, so tests can assert exact parent relationships while using
real SDK spans. The resource supplies `service.name`, `service.version`, and
`deployment.environment.name` once for every signal.

Use `TelemetryRuntime.in_memory(sample_ratio=1.0)` for deterministic tests.
The sampler is parent based. An unsampled inbound parent remains unsampled,
while metrics are still recorded. A ratio of `0.1` samples about one tenth of
new root traces. Traces explain examples; metrics measure the whole traffic
population.

## Send OTLP to a local collector and Jaeger

`ExporterSettings.from_environment()` reads standard deployment settings.
`TelemetryRuntime.for_otlp(settings)` creates gRPC OTLP trace, metric, and log
exporters, batching span and log processors, and a periodic metric reader.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_INSECURE=true
```

Point the application at an OpenTelemetry Collector OTLP gRPC receiver. A
minimal collector pipeline receives OTLP, batches spans, and sends them to
Jaeger:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
    tls:
      insecure: true
  debug:
processors:
  batch:
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/jaeger]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [debug]
```

Send metrics to a metric backend by adding a metric exporter and a `metrics`
pipeline to the collector. Open Jaeger's search page, select the `relay`
service, submit a task, and inspect `POST /tasks` with its
`relay.storage` child. The local gate intentionally constructs only in-memory
providers, so an unavailable collector cannot make tests slow or flaky.

In production, bound the collector queue and retry policy. Monitor dropped
spans, logs, and exporter failures. Call `TelemetryRuntime.shutdown()` during
an orderly process stop so all three providers get a chance to flush.

## Python REPL debugging session

Confirm that the installed package, public telemetry types and constructor
match this checkpoint before inspecting an in-memory runtime:

```pycon
>>> import inspect
>>> import lab_34_instrumented_service as lab
>>> lab.__file__
'.../lab_34_instrumented_service/__init__.py'
>>> "TelemetryRuntime" in dir(lab)
True
>>> lab.TelemetryRuntime
<class 'lab_34_instrumented_service.telemetry.TelemetryRuntime'>
>>> inspect.signature(lab.TelemetryRuntime.in_memory)
<Signature (*, service_name: 'str' = 'relay', service_version: 'str' = '0.1.0', sample_ratio: 'float' = 1.0, ids: 'IdGenerator | None' = None) -> 'TelemetryRuntime'>
>>> runtime = lab.TelemetryRuntime.in_memory()
>>> runtime.resource.attributes["service.name"]
'relay'
>>> runtime.shutdown()
```

If the import path points at another checkout, leave the REPL, activate the
intended environment and reinstall this lab. Inspect resource attributes rather
than exporter internals because the resource is the supported identity boundary
shared by traces, metrics and logs.

## Send to Application Insights

Application Insights is the application view in Azure Monitor. The optional
`TelemetryRuntime.for_azure_monitor(settings)` adapter uses
`AzureMonitorTraceExporter`, `AzureMonitorMetricExporter`, and
`AzureMonitorLogExporter`.

```bash
export APPLICATIONINSIGHTS_CONNECTION_STRING='InstrumentationKey=...'
```

Create the runtime only in application startup. Tests leave this variable
unset and verify that missing configuration fails before any network request.
Prefer a collector or supported identity-based configuration when it lets the
application avoid a stored connection string. Keep deployment credentials out
of source control.

In Application Insights, server spans appear as requests and client spans as
dependencies. Their trace ID maps to the operation ID. The saved
`artifacts/relay_diagnosis.kql` query runs in the linked Log Analytics
workspace. It removes health traffic, joins `AppRequests` to
`AppDependencies`, and groups failures by dependency target. Start from an
alert window, select one operation ID, then inspect the ordered request and
dependency records.

## Context across execution boundaries

W3C `traceparent` carries trace identity and sampling flags. W3C `baggage`
carries small application context. This lab allowlists only
`relay.workflow`, limits its length, and supplies `inject_context()` and
`extract_context()` for message properties.

Normal `asyncio` task creation copies Python `contextvars`. Work sent to a raw
thread may need `contextvars.copy_context()` and `Context.run()`. A child
process cannot share in-memory context, so inject context into process or job
arguments and extract it after startup. For a queue, inject into message
properties rather than the body. Extract before starting the consumer span.
Use a span link when a batch has several producing traces because a span has
only one parent.

Test every boundary used by the service. Accidental propagation is common when
tests run in one event loop and production uses a thread pool or broker.

## Security and cardinality

Treat telemetry as exported data. Do not record request bodies, authorization
headers, cookies, access tokens, connection strings, or unrestricted exception
text. Allowlist baggage keys at trust boundaries. An external caller can
otherwise increase storage cost or place private values in a wider analysis
system.

Metric attributes must have bounded value sets. Route templates, outcomes, and
dependency names are suitable. Task IDs, tenant IDs, full URLs, user text, and
random values create a new time series for each value. Relay therefore records
only route, outcome, and dependency on its counter and histogram. The tenant
remains on the selected request span for trace lookup, but not on metrics or
logs. Deployments with sensitive tenant names should replace it with a
controlled classification or omit it.

## What the tests prove

The tests inspect actual SDK `ReadableSpan`, `MetricsData`, and
`ReadableLogRecord` values. They check resource identity, server and client
kinds, parentage, error status, bounded metric dimensions, log severity and
trace correlation, W3C propagation, baggage, parent-based sampling, quiet
health routes, provider shutdown, exporter settings, and the Log Analytics
query contract. This proves emission and causality. A staging synthetic
request is still needed to prove that a collector and chosen backend ingest
the data.
