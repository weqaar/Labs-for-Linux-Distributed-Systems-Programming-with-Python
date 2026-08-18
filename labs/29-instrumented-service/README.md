# Lab 29 Instrumented Service

This checkpoint instruments relay requests without needing Azure Monitor in the
local test run. The code exports structured logs, counters, histograms, traces,
and a representative KQL query that explains how to diagnose a bad dependency.

## Components

- `InstrumentedRelayService` emits OpenTelemetry-shaped spans, metrics, and
  logs for relay task submissions.
- `parse_traceparent` and `format_traceparent` propagate W3C trace context to a
  downstream dependency.
- `InMemoryTelemetry` captures spans, metrics, and logs for tests.
- `artifacts/relay_diagnosis.kql` shows the query used to find the failing
  dependency while suppressing liveness and readiness noise.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```
