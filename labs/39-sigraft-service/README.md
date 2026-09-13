# Lab 39 SigRaft Service

This final lab assembles the contracts developed through the `relay`
labs as the SigRaft service. It includes a runnable REST API, the `sigraftctl`
client, a GraphQL endpoint, resource-aware job placement, release artifacts
that prove the same digest reaches staging and production, OpenTelemetry
evidence, both Azure and on-prem cloud targets, and offline checks for rollback,
agent convergence and post-deploy verification.
The runnable service uses OpenTelemetry SDK traces, metrics and correlated
logs, plus validated transactional TOML hot reload.

Release evidence also retains the earlier FastAPI/Uvicorn/optional-uvloop
boundary and the bounded Ray compute lab. The compact final HTTP process
remains deliberately small; those entries show that their independently
runnable labs passed rather than suggesting that Lab 39 replaces its server or
starts a Ray cluster.

## Run the compact SigRaft service

```bash
python -m lab_39_sigraft_service.sigraft_service --port 8081 \
  --digest sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --config config.example.toml --environment local
python -m lab_39_sigraft_service.sigraftctl \
  --base-url http://127.0.0.1:8081 metadata
python -m lab_39_sigraft_service.sigraftctl \
  --base-url http://127.0.0.1:8081 submit "inspect cluster"
```

## REST and GraphQL over one task model

`POST /tasks` and `GET /tasks/{task_id}` remain the direct REST resource
interface. `POST /graphql` exposes typed queries and mutations over the same
`SigRaftService` methods, so validation and task state cannot drift between
interfaces. GraphQL reads require the `tasks:read` scope and mutations require
`tasks:write`, supplied in the `X-SigRaft-Scopes` header.

```bash
sigraftctl --base-url http://127.0.0.1:8081 graphql \
  --scope tasks:read \
  '{ tasks(first: 10) { id action state } }'
```

GraphQL returns operation errors beside its `data` member while HTTP status
reports whether the endpoint accepted the transport request. Request bodies
and list sizes remain bounded.

## Resource-aware scheduling

The final service carries Lab 38's placement boundary through
`/scheduler/nodes`, `/scheduler/jobs`, `/scheduler/run`, and
`/scheduler/jobs/{task_id}`. Nodes report CPU, memory, GPU, label and NUMA
inventory. The elected scheduler validates requests, excludes stale nodes,
makes deterministic placements, records an allocation and returns a
node-specific dispatch plan containing cgroup, CPU, memory, NUMA and GPU
enforcement values. The queue transports that committed decision; it does not
select the node.

## Python REPL debugging session

The HTTP adapters use ordinary domain methods that can be inspected before
starting a listener:

```pycon
>>> import lab_39_sigraft_service as lab
>>> service = lab.SigRaftService(release_digest="sha256:" + "a" * 64)
>>> task = service.submit_task("inspect cluster")
>>> (task.task_id, task.state)
('task-1', 'queued')
>>> result = service.graphql.execute(
...     "{ tasks(first: 10) { id action state } }",
...     scopes=frozenset({"tasks:read"}),
... )
>>> result.data
{'tasks': [{'id': 'task-1', 'action': 'inspect cluster', 'state': 'QUEUED'}]}
>>> service.shutdown()
```

Inspect `result.errors`, `service.scheduler`, and
`service.config_manager.status` when an API or scheduling test fails.

From the repository root, the same final lab runs with the shared local
resource stack:

```bash
make local-check
make local-up
python -m lab_39_sigraft_service.sigraftctl \
  --base-url http://127.0.0.1:8080 metadata
```

The local stack uses open-source storage, database, cache, messaging and
telemetry services. Start its observability profile to ingest OTLP traces,
metrics and logs into Jaeger, Prometheus and Loki, then analyze all three
through Grafana:

```bash
docker compose --file local/compose.yaml --profile observability up --detach
```

Azure Pipelines and AKS remain one release target. The SigRaft on-prem cloud
from Labs 36 and 37 is the second. Both deploy the same service contract and
content-addressed image. `artifacts/onprem-release.yml` is the checked,
pipeline-neutral plan for Harbor and the on-prem Kubernetes contexts.

## OpenTelemetry in the runnable service

The service creates real `TracerProvider`, `MeterProvider`, and
`LoggerProvider` instances. Every non-health HTTP request receives a server
span. The handler extracts an inbound W3C `traceparent`, records bounded route,
method, and status dimensions on its counter and histogram, and emits an SDK
log record while the server span is current. The log exporter therefore
receives the same trace and span IDs without copied correlation fields.

All signals carry one resource:

| Attribute | Source |
|---|---|
| `service.name` | Fixed to `sigraft` |
| `service.version` | Installed package version |
| `deployment.environment.name` | TOML identity or `--environment` override |
| `service.instance.id` | TOML identity or `--instance-id` override |
| `service.release.digest` | Required `--digest` value |

`/livez` and `/readyz` emit no application spans, measurements, or request
logs. This prevents the local health probe from dominating low-traffic
telemetry. Tests construct in-memory SDK exporters and readers. The example
configuration selects OTLP over gRPC at `otel-collector:4317`, which is the
collector service in `local/compose.yaml`.

## Operational analysis in the runnable service

`GET /analysis` serves an HTML report generated with NumPy, pandas, Matplotlib,
SciPy, and statsmodels. The packaged observations make the lab runnable
offline. Set `analysis.observations_path` to a bounded CSV exported from the
telemetry ingestion path for a real deployment. The fixed schema rejects extra
columns, unsafe dimensions, malformed timestamps, and non-finite numbers.

The report shows per-release count, failure rate, mean, median, p95, p99, and
sample standard deviation. It also shows a t interval for the overall mean, a
Mann-Whitney comparison with rank-biserial effect size, an OLS model, and a
Durbin-Watson residual diagnostic. The standalone service is:

```bash
sigraft-analysis serve --port 8082
```

The final HTTP process owns the same `AnalyticsRuntime`. Its managed refresh
thread prepares a complete replacement report before configuration commit,
publishes it under one lock, retains the old report on refresh failure, and
joins during shutdown. Metadata exposes status, row count, input revision, and
refresh time, not the configured filesystem path.

## Read-only Redfish inventory

The final lab exposes a deliberately small data-centre inventory at
`/redfish/v1/`, `/redfish/v1/Systems`, and
`/redfish/v1/Systems/{system_id}`. It gives SREs one typed view of managed
system identity, power state and health alongside service metadata. The local
fixture represents one virtual rack node; a deployment composition root can
replace it with inventory collected through the bounded Redfish client from
Lab 37.

This endpoint is not a transparent BMC proxy and does not provide complete
Redfish service conformance. It exposes no reset, power, firmware or account
actions. Hardware-control credentials remain outside the SigRaft HTTP service.

Install the optional direct Azure Monitor adapter with:

```bash
pip install -e ".[azure]"
```

Then set `telemetry.exporter` to `azure` and provide
`telemetry.azure_connection_string` through a protected configuration source.
The adapter creates Azure Monitor trace, metric, and log exporters. Tests do
not provide credentials or contact Azure.

## Validated TOML and hot reload

`config.example.toml` is a complete checked-in candidate. SigRaft rejects
unknown fields, missing tables, unsupported schema versions, invalid exporter
names, invalid URLs, unsafe request limits, and sampling ratios outside zero
through one. Its revision is the SHA-256 digest of the complete file bytes.
Generation one is startup. Each different candidate that commits successfully
increments the generation.

The metadata endpoint exposes only revision, generation, result, a safe error
class, and restart-required field names:

```json
{
  "configuration": {
    "generation": 2,
    "revision": "17b1...",
    "last_result": "applied",
    "last_error": null,
    "restart_required": ["telemetry.otlp_endpoint"]
  }
}
```

Reload is a two-phase operation. Every registered subsystem first validates
and prepares the whole candidate without publishing visible state. If every
prepare succeeds, commits run. Any prepare or commit failure rolls prepared
changes back in reverse order and leaves the old snapshot and generation
active. `RequestLimits` and `ReloadableSampler` implement this hook. The
analytics refresh thread registers the same `prepare`, `commit`, and `rollback`
contract rather than inventing a separate reload path.

These fields change live:

| Field | Effect |
|---|---|
| `requests.max_body_bytes` | Rejects larger request bodies before reading them |
| `requests.max_action_chars` | Bounds accepted task actions |
| `telemetry.sample_ratio` | Changes decisions for new root traces through a thread-safe sampler |

Exporter type, OTLP destination, transport security, and Azure connection
string are restart-required. Deployment environment and service instance
identity also require restart because one process must retain one resource
identity. A successful parse reports those names in metadata but does not
replace active providers. Replacement would need coordinated flush, provider
swap, and shutdown semantics.

With configuration watching enabled, a managed thread compares a content
digest rather than relying only on timestamp resolution. It reads and
validates the whole candidate before reload. Disable polling with
`--no-watch-config`; explicit reload remains available. Send `SIGHUP` to
request reload. The signal handler only sets thread events, while the managed
thread performs file I/O and subsystem work.

```bash
kill -HUP "$(cat /run/sigraft.pid)"
```

Call `ConfigWatcher.request_reload()` when an administrative control plane
already runs inside the process. Shutdown wakes and joins the watcher before
closing all three telemetry providers.

## Release artifacts

- `artifacts/azure-pipelines.yml` gates the exact commit, previews and converges
  staging and production Bicep, builds once, promotes one digest, verifies each
  host, and rolls back to the previous digest.
- `artifacts/onprem-release.yml` builds one OCI artifact, publishes it to
  Harbor, deploys the resolved digest through staging and production, gates on
  operational analysis, requires an external approval, and restores the
  previous digest on failure.
- `artifacts/self_hosted_agents.yml` converges self-hosted agents without shell
  tasks.
- `artifacts/release-evidence.json` records the commit, the passing quality
  gates, the Copier and executable build contracts, task-time semantics,
  object interfaces, tree and graph invariants, performance diagnostics,
  Python execution, multicore and NUMA contracts, typed query and CPython
  instruction contracts, bounded stream buffering, offline TCP/IP packet
  encapsulation and decapsulation, Celery and Valkey delivery, ZeroMQ,
  WebSocket and GraphQL APIs, Bicep and AKS infrastructure, Nginx,
  autoscaling, disruption budgets, progressive delivery and the promoted
  digests. It also records OpenTelemetry export, the OpenStack/Kubernetes
  on-prem cloud contracts, operational analysis, and transactional
  configuration reload.
- `src/lab_39_sigraft_service/fabric_executor.py` verifies deployed hosts and
  fails the stage when any host is wrong.

## SigRaft product map

| Stage | Lab | Product contribution |
|---|---|---|
| 01 | `01-first-service` | Starts the first relay process and request loop. |
| 02 | `02-package-build` | Generates the relay contract and builds installable plus ELF and PE artifacts. |
| 03 | `03-quality-gate` | Adds reproducible lint, type, and test gates. |
| 04 | `04-cli-tool` | Introduces the first `relayctl` command surface. |
| 05 | `05-object-oriented-design` | Establishes domain values, interfaces, adapters, and polymorphic handlers. |
| 06 | `06-trees-and-graphs` | Adds a balanced priority index, dependency DAG, and route graph. |
| 07 | `07-performance-diagnostics` | Adds repeatable profiling, benchmarking, and Linux diagnostic plans. |
| 08 | `08-azure-resources` | Defines Bicep modules for storage, ACR, AKS, monitoring, identity and RBAC. |
| 09 | `09-azure-sdk` | Moves cloud access onto SDK calls and managed credentials. |
| 10 | `10-python-execution` | Connects source, AST, RISC-V ADD, bytecode, VM, and host architecture. |
| 11 | `11-worker-pool` | Adds bounded workers, IPC queues, shared memory, multicore and NUMA evidence. |
| 12 | `12-echo-service` | Establishes the socket loop and traces Ethernet, IPv4 and TCP encapsulation. |
| 13 | `13-packet-tools` | Adds packet inspection helpers for wire debugging. |
| 14 | `14-native-extension` | Speeds a hot path with a native parser. |
| 15 | `15-cpython-bytecode` | Adds typed task queries and a tested custom interpreter instruction. |
| 16 | `16-framed-protocol` | Frames traffic through a bounded ring buffer. |
| 17 | `17-wire-format` | Defines versioned serialization for relay messages. |
| 18 | `18-validated-models` | Validates task payloads before work starts. |
| 19 | `19-rpc-service` | Exposes remote task submission as an RPC service. |
| 20 | `20-zeromq-patterns` | Adds real PUB/SUB sockets and Celery background work over Valkey. |
| 21 | `21-websocket-service` | Adds resumable WebSockets and typed GraphQL operations. |
| 22 | `22-logical-clocks` | Normalizes civil time and preserves causal order across replicas. |
| 23 | `23-quorum-basics` | Adds quorum reads and writes for shared state. |
| 24 | `24-raft-election` | Elects a relay leader. |
| 25 | `25-replicated-log` | Replicates the durable task log. |
| 26 | `26-distributed-lock` | Coordinates maintenance with distributed locks. |
| 27 | `27-stateless-service` | Splits stateless front ends from stored state. |
| 28 | `28-partitioned-store` | Shards the store behind relay. |
| 29 | `29-kv-store` | Persists relay state in a key-value layer. |
| 30 | `30-dag-engine` | Executes task dependencies as a DAG. |
| 31 | `31-airflow-dags` | Orchestrates relay flows with scheduler DAGs. |
| 32 | `32-container-deploy` | Adds local and AKS delivery, Nginx, scaling and versioned rollouts. |
| 33 | `33-load-shedding` | Bounds queueing and retries under overload. |
| 34 | `34-instrumented-service` | Adds telemetry, trace propagation, and diagnosis queries. |
| 35 | `35-operational-data-analysis` | Turns observations into statistical summaries and a web report. |
| 36 | `36-on-prem-cloud` | Plans the configurable SigRaft on-prem cloud. |
| 37 | `37-on-prem-cloud-apis` | Reconciles OpenStack, Kubernetes and registry resources through Python APIs. |
| 38 | `38-resource-scheduling` | Adds resource admission, deterministic placement, reservations and node dispatch. |
| 39 | `39-sigraft-service` | Assembles SigRaft, exposes REST and GraphQL, schedules jobs, exports telemetry, promotes one digest and proves rollback. |

## Quality gates

```bash
pybootstrap check
```
