# Lab 34 Instrumented Service

This lab captures what happens while SigRaft handles a request. Its Python
import name is `relay`; the program does not relay traffic.
OpenTelemetry traces connect timed operations called spans, metrics count
requests and measure duration, and logs describe individual events. Shared
trace identifiers let you find the logs belonging to one request.

The implementation uses real OpenTelemetry SDK providers with in-memory
exporters and a metric reader. Tests inspect their output without a collector,
Azure account, network or credentials.

## Goal and working order

Follow one request across a service and dependency boundary using three
different signals: traces, metrics and logs. You will inspect real SDK output
from the provided instrumented handler before choosing an optional exporter.
The handler is called directly in this lab; no HTTP listener or job executor
is installed.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
OpenTelemetry API, SDK and OTLP gRPC exporters are declared in
`pyproject.toml`; Azure Monitor is optional.

1. Install and construct the in-memory runtime in the REPL.
2. Run `pytest -q tests/test_lab_34_instrumented_service.py`. Trace the
   server span, client child, correlated log and metric attributes in the
   request test.
3. Compare a sampled request with an unsampled inbound parent. Metrics must
   still count the request; dropped traces do not mean missing traffic.
4. Change a fake dependency outcome and inspect error status without exporting
   sensitive request or exception data.

## Install and run the gates

```bash
python3 -m venv .venv
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
6. adds the duration in milliseconds to the `relay.request.duration` histogram; and
7. emits an SDK log record correlated with the server trace and span IDs.

The manual clock controls the measured duration. `SequentialIdSource` is an
SDK `IdGenerator`, so tests can assert exact parent relationships while using
real SDK spans. The resource supplies `service.name`, `service.version`, and
`deployment.environment.name` once for every signal.

Use `TelemetryRuntime.in_memory(sample_ratio=1.0)` for deterministic tests.
The sampler is parent based. An unsampled inbound parent remains unsampled,
while metrics are still collected. A ratio of `0.1` samples about one tenth of
new root traces. Use traces to inspect sampled requests and metrics to count requests whether
or not their traces were sampled.

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

The YAML is a deployment fragment, not a collector started by this lab.
To generate data, compose `InstrumentedRelayService` with the OTLP runtime
and call `handle_request` using the request and fake dependency from the tests.
Setting the environment variables alone sends nothing.

Send metrics to a metric backend by adding a metric exporter and a `metrics`
pipeline to the collector. Open Jaeger's search page, select the `relay`
service, invoke the handler, and inspect `POST /tasks` with its
`relay.storage` child. The local gate intentionally constructs only in-memory
providers, so an unavailable collector cannot make tests slow or flaky.

In production, bound the collector queue and retry policy. Monitor dropped
spans, logs, and exporter failures. Call `TelemetryRuntime.shutdown()` during
an orderly process stop so all three providers get a chance to flush.

## Python REPL debugging session

Confirm that the installed package, public telemetry types and constructor
match this lab before inspecting an in-memory runtime:

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
than exporter internals: those attributes identify the service consistently in
traces, metrics and logs.

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
dependency rows.

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

Treat telemetry as exported data. Do not store request bodies, authorization
headers, cookies, access tokens, connection strings, or unrestricted exception
text. Allowlist baggage keys at trust boundaries. An external caller can
otherwise increase storage cost or place private values in a wider analysis
system.

Metric attributes must have bounded value sets. Route templates, outcomes, and
dependency names are suitable. Task IDs, tenant IDs, full URLs, user text, and
random values create a new time series for each value. Relay therefore sets
only route, outcome, and dependency attributes on its counter and histogram. The tenant
remains on the selected request span for trace lookup, but not on metrics or
logs. Deployments with sensitive tenant names should replace it with a
controlled classification or omit it.

## What the tests check

The tests inspect actual SDK `ReadableSpan`, `MetricsData`, and
`ReadableLogRecord` values. They check resource identity, server and client
kinds, parentage, error status, bounded metric dimensions, log severity and
trace correlation, W3C propagation, baggage, parent-based sampling, quiet
health routes, provider shutdown, exporter settings, and the Log Analytics
query's required clauses. These checks inspect emitted values and trace parent
relationships. Send a synthetic request in staging and locate its trace, metrics
and logs in the chosen backends to verify ingestion.

## Tests

The lab has two kinds of test. `tests/test_lab_34_instrumented_service.py`
holds the unit tests. They check single helpers and settings in isolation,
such as `traceparent` parsing, the sampler, baggage limits, exporter settings
and the KQL query checks.

`tests/test_functional.py` holds the functional tests. They build an
`InstrumentedRelayService` on `TelemetryRuntime.in_memory()` and send whole
`POST /tasks` requests through `handle_request`. They then check that each
request produces a correlated trace, metric and log, that a dependency
timeout returns 502 and marks every signal as failed, that job IDs such as
`task-17` stay out of metric labels, and that message context continues the
producer's trace. All telemetry goes to in-memory exporters, so no collector
is needed.

Run each kind alone, or run both with the gate:

```bash
pytest tests/test_lab_34_instrumented_service.py
pytest tests/test_functional.py
pybootstrap check
```

## Contribution and completion

This lab demonstrates telemetry emission for the
SigRaft job-orchestration web service. Lab 39 implements its own runtime with
real SDK providers; it does not import this lab.
Finish when you can relate all three signals without putting task IDs into
metric labels, with `pybootstrap check` exit 0. Exit 1 means findings;
exit 2 means a gate could not run. Always call `runtime.shutdown()`.
Stop collectors or local-stack services you started, and remove only owned
Azure resources after optional live validation.
