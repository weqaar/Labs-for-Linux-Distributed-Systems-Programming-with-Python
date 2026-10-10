# Lab 39 SigRaft Service

## Goal and purpose

This final lab integrates the architectural patterns from earlier chapters into
the runnable SigRaft job-orchestration web service and its operator CLI
(`sigraftctl`).

The lab demonstrates the complete production lifecycle of the service:
1. **Multi-protocol service APIs:** SigRaft exposes unified task management over
   HTTP REST (`/tasks`), GraphQL query and mutation endpoints, and real-time
   authenticated WebSocket streams.
2. **Resource scheduling and placement:** Allocating jobs across nodes based on
   CPU, memory, and hardware accelerator constraints while preventing
   oversubscription.
3. **Telemetry and observability:** End-to-end OpenTelemetry instrumentation
   exporting structured spans, Prometheus metrics, and correlated logs.
4. **Configuration hot-reloading:** Safe, transactional TOML configuration
   updates without process restarts or inconsistent states.
5. **Release verification:** Offline verification of release evidence, canary
   promotion manifests, and post-deployment validation reports.

## Learning outcomes

By completing this lab, you will understand:
- How to structure a production-grade Python web service supporting multiple API protocols.
- How to implement transactional configuration reload without downtime.
- How to connect OpenTelemetry traces, metrics, and logs across distributed service requests.
- How to verify build, container, and deployment evidence before promoting software to production.

## Prerequisites and setup

Operate the supplied compact SigRaft job-orchestration web service and connect
its responses to release checks. You will submit and read a job,
compare interfaces, inspect configuration changes and test release files.
The reference implementation is already present; the exercises extend tests
and documentation rather than ask you to assemble 38 installed packages.

Use Python 3.10 or later from this directory. Read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Create an isolated environment so this lab does not pick up command names installed by another lab.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The HTTP listener uses the standard library; graphql-core, websockets,
OpenTelemetry and the numerical analysis packages are real runtime
dependencies. PyYAML and TOML parsing support release/configuration artifacts.
All dependencies are declared in `pyproject.toml`; Sphinx is a development
tool and Azure Monitor is optional. Default tests need no Azure subscription,
Docker or external collector, but loopback connections must be allowed.

This service stores jobs and scheduler allocations in process memory. It does
not execute submitted action text, recover jobs after process loss, elect a
Raft leader or enforce returned node plans. Release artifact validation is not
proof that a pipeline ran or a cloud rollout succeeded. Keep the listener on
loopback unless a separately reviewed deployment supplies its trust boundary;
the HTTP scope header in the examples is not authenticated identity.

1. Start with the REPL example to inspect shared REST/GraphQL domain state.
2. Run the compact service below in one terminal and the CLI in another
   activated terminal. Use the returned task ID for status, not a guessed ID.
3. Run `pytest -q tests/test_lab_39_sigraft_service.py tests/test_config.py`.
   Compare transport results, scheduler plans and transactional reload failure.
4. Build documentation and run its exercises. Optional WebSocket, telemetry
   backends and platform deployment each require their own setup and test results.

## Run the compact SigRaft service

The checked-in configuration selects a Compose-network collector hostname.
For this standalone offline exercise, create a local copy with in-memory
telemetry rather than try to contact that collector:

```bash
python - <<'PY'
from pathlib import Path
source = Path("config.example.toml").read_text()
Path("config.local.toml").write_text(source.replace('exporter = "otlp"', 'exporter = "memory"'))
PY
python -m lab_39_sigraft_service.sigraft_service --port 8081 \
  --digest sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --config config.local.toml --environment local
```

In the second terminal:

```bash
python -m lab_39_sigraft_service.sigraftctl \
  --base-url http://127.0.0.1:8081 metadata
python -m lab_39_sigraft_service.sigraftctl \
  --base-url http://127.0.0.1:8081 submit "inspect cluster"
```

The repeated `a` digest is an exercise value, not the digest of a built image.
Metadata should report it, and submission should report a queued job.
Run `sigraftctl --base-url http://127.0.0.1:8081 status` with the identifier
the service returned. The state remains queued until trusted application
code explicitly reports a transition.

## REST and GraphQL

`POST /tasks` and `GET /tasks/{task_id}` are the REST paths. The code calls a
job identifier `task_id`.
`POST /graphql` exposes typed queries and mutations over the same
`SigRaftService` methods, so validation and job state cannot drift between
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

## WebSocket commands and status updates

Start the optional loopback listener with `--websocket-port 8082` and
`--websocket-credentials /path/to/private-credentials.json`. It shares the HTTP
service's jobs but uses a separate port, so the existing HTTP listener
and commands remain unchanged. The [usage guide](docs/usage.rst) shows how to
generate private random credentials; do not put tokens in command arguments,
URLs or source control.

```bash
sigraftctl --base-url ws://127.0.0.1:8082 \
  --transport websocket --token-file "$HOME/.config/sigraft/ws-token" \
  submit "count failed requests"
sigraftctl --base-url ws://127.0.0.1:8082 \
  --transport websocket --token-file "$HOME/.config/sigraft/ws-token" \
  status task-1 --watch
```

Use the returned identifier, not necessarily `task-1`. Watch prints an initial
snapshot, then real state changes from `SigRaftService.transition_task`.
That method is for a trusted executor or the explicit REPL exercise, not a
client command. The service does not yet execute submitted actions or provide
output-file retrieval. Without an executor reporting changes, a job stays
queued. Scheduler allocations remain a separate state model.

The [protocol guide](docs/protocol.rst) defines `sigraft.jobs.v1`, request IDs,
server-derived scopes, subscription limits and shutdown. It is not the
GraphQL-specific subprotocol taught in Lab 21. WebSocket GraphQL requests use
the same resolvers as HTTP; caller-supplied scopes cannot escalate access.
No command is automatically retried after a disconnect. Reopen a watch for a
current snapshot, not a replay of missed events.

## Documenting and serving the Python API

The running interfaces need instructions that stay aligned with their Python
methods. Build the supplied documentation, execute its examples and preview
the generated pages locally:

```bash
python -m pip install -e ".[dev]"
python -m sphinx -n -W --keep-going -b html docs build/docs/html
python -m sphinx -W -b doctest docs build/docs/doctest
python -m http.server --bind 127.0.0.1 --directory build/docs/html 8000
```

Open `http://127.0.0.1:8000/`, inspect the generated API and versioned footer,
then stop the preview with Ctrl+C. The [documentation exercise](docs/documentation.rst)
asks you to extend a public method's docstring, explain an invariant with an
inline comment, and verify that a wrong example or reference fails the gate.
Do not comment obvious assignments or duplicate type annotations in prose
without explaining their meaning.

The pytest gate builds HTML, executes doctests, tests broken documentation in
temporary copies and retrieves the generated site over loopback HTTP.
`pybootstrap check` must exit zero with all four gates executed. The release
plans retain the docs as a commit-labelled artifact. Static hosting and
external link checking are separate deployment steps, not claims made by the
offline gate.

## Resource-aware scheduling

The final service implements a separate version of Lab 38's placement model through
`/scheduler/nodes`, `/scheduler/jobs`, `/scheduler/run`, and
`/scheduler/jobs/{task_id}`. Nodes report CPU, memory, GPU, label and NUMA
inventory. The configured leader identity gates placement; no election runs.
The scheduler validates requests, excludes stale nodes,
makes deterministic placements, stores an allocation and returns a
node-specific dispatch plan containing cgroup, CPU, memory, NUMA and GPU
enforcement values. These are returned data, not actual cgroup enforcement or
a broker publish. A future queue adapter would carry that decision to the
selected node rather than choose a node itself.

As in Lab 38, explicit CPU affinity is not checked against node inventory
unless the NUMA filter removes the unknown CPU. The current implementation
can return CPU 99 for a node reporting only CPUs 0 and 1. This is a validation
gap, not a supported placement. Scheduler states such as `scheduled` and
`cancelled` are distinct from the four states exposed for `/tasks`.

## Tests

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

Several modules mix both kinds. The unit tests check configuration reload,
analytics, telemetry setup, job events, release artifacts, documentation
release steps and the observation probe with deterministic fakes.

The functional tests start the SigRaft service with `run_server` or
`run_websocket_server` on 127.0.0.1 with port 0, or run the `sigraftctl`
entry point `main(argv)`. In `tests/test_lab_39_sigraft_service.py` they are
`test_sigraft_service_and_client_contract_work_end_to_end`, the
`test_graphql_` tests, the `test_resource_scheduler_` tests,
`test_read_only_redfish_service_exposes_bounded_system_inventory` and the
`test_invalid_` tests. In `tests/test_websocket_transport.py` they are the
tests using the `hosted` fixture and the two `test_cli_` tests. The others
are `test_body_limit_changes_live_without_restarting_http`,
`test_integrated_analysis_route_has_safe_headers`, the HTTP tests in
`tests/test_telemetry.py` and `test_docs_can_be_served_locally`.

```bash
pytest tests/test_lab_39_sigraft_service.py -k "end_to_end or graphql or resource_scheduler or redfish_service or invalid"
pytest tests/test_websocket_transport.py tests/test_telemetry.py
pytest tests/test_config.py tests/test_analytics_service.py
```

`pybootstrap check` runs both kinds of test in its test gate.

## Python REPL debugging session

The HTTP adapters use ordinary domain methods that can be inspected before
starting a listener:

```pycon
>>> import lab_39_sigraft_service as lab
>>> lab.__name__, lab.__file__
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

The GraphQL query sees the same queued job created through the Python
service method. Its enum spelling is uppercase in GraphQL, while the Python
object uses lowercase; both describe the same state.
Inspect `result.errors`, `service.scheduler`, and
`service.config_manager.status` when an API or scheduling test fails.

## Run with the shared local stack

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
span. The handler extracts an inbound W3C `traceparent`, sets bounded route,
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

For direct Azure Monitor export, install the optional adapter:

```bash
pip install -e ".[azure]"
```

Then set `telemetry.exporter` to `azure` and provide
`telemetry.azure_connection_string` through a protected configuration source.
The adapter creates Azure Monitor trace, metric, and log exporters. Tests do
not provide credentials or contact Azure.

## Operational analysis in the runnable service

`GET /analysis` serves an HTML report generated with NumPy, pandas, Matplotlib,
SciPy, and statsmodels. The packaged observations make the lab runnable
offline. Set `analysis.observations_path` to a bounded CSV exported from the
telemetry ingestion path for a real deployment. The fixed schema rejects extra
columns, unsafe dimensions, malformed timestamps, and non-finite numbers.

As in Lab 35, use the report to describe the collected sample, not to infer
that a release caused every observed difference. The report shows per-release count, failure rate, mean, median, p95, p99, and
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

The optional `observation_probe.py` measures `POST /tasks` submission latency.
Its `succeeded` outcome means submission succeeded, not that the job completed,
and its queue-depth field is always zero rather than measured. Do not combine
these rows with Lab 35's completed-attempt observations as if their durations
and outcomes meant the same thing.

The release-analysis command validates and reports a caller-supplied candidate
digest; it does not establish which image produced the observations. A real
promotion decision needs separate proof linking the dataset to that image.

## Read-only Redfish inventory

The final lab exposes a deliberately small data-centre inventory at
`/redfish/v1/`, `/redfish/v1/Systems`, and
`/redfish/v1/Systems/{system_id}`. It gives SREs one typed view of managed
system identity, power state and health alongside service metadata. The local
fixture represents one virtual rack node; deployment startup code can
replace it with inventory collected through the bounded Redfish client from
Lab 37.

This endpoint is not a transparent BMC proxy and does not provide complete
Redfish service conformance. It exposes no reset, power, firmware or account
actions. Hardware-control credentials remain outside the SigRaft HTTP service.

## Validated TOML and hot reload

Change a request limit without losing the last valid configuration if the
replacement is malformed. The reload mechanism prepares changes first, then
publishes them together or restores the previous state.

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
kill -HUP PID
```

Replace `PID` with the specific service process ID obtained from your terminal
or process supervisor. This standalone command does not create a PID file.

To exercise this path, change `telemetry.sample_ratio` in `config.local.toml`
to another value between zero and one. Request reload and use the CLI
`metadata` command to inspect the increased generation. Then try `2.0`:
validation must reject it while the previous generation remains active.
Restore the valid value before continuing. No backend is needed for this
experiment with the in-memory exporter.

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
- `artifacts/release-evidence.json` lists the commit, the passing quality
  gates, the Copier and executable build contracts, task-time semantics,
  object interfaces, tree and graph invariants, performance diagnostics,
  Python execution, multicore and NUMA contracts, typed query and CPython
  instruction contracts, bounded stream buffering, TCP and UDP socket
  contracts, offline TCP/IP and UDP/IP packet encapsulation and decapsulation,
  Celery and Valkey delivery, ZeroMQ,
  WebSocket and GraphQL APIs, Bicep and AKS infrastructure, Nginx,
  autoscaling, disruption budgets, progressive delivery and the promoted
  digests. It also lists OpenTelemetry export, the OpenStack/Kubernetes
  on-prem cloud contracts, operational analysis, and transactional
  configuration reload.
- `src/lab_39_sigraft_service/fabric_executor.py` verifies deployed hosts and
  fails the stage when any host is wrong.

These files are examples to validate and adapt, not proof of a completed
deployment. The self-hosted-agent playbook declares a service that runs
`sigraft_agent`, but does not install that module. Supply and verify the agent
before starting the declared service. The release JSON's commit and digests
must be replaced with values from the actual build and its gate results.

## SigRaft product map

| Stage | Lab | Product contribution |
|---|---|---|
| 01 | `01-first-service` | Defines an offline in-memory lifecycle, without a listener or CLI. |
| 02 | `02-package-build` | Builds packages, executable artifacts and a Sphinx API documentation site. |
| 03 | `03-quality-gate` | Adds formatting, PEP conventions, type checks and executable documentation gates. |
| 04 | `04-cli-tool` | Introduces the first `relayctl` command surface. |
| 05 | `05-object-oriented-design` | Establishes domain values, interfaces, adapters, and polymorphic handlers. |
| 06 | `06-trees-and-graphs` | Adds a balanced priority index, dependency DAG, and route graph. |
| 07 | `07-performance-diagnostics` | Adds repeatable profiling, benchmarking, and Linux diagnostic plans. |
| 08 | `08-azure-resources` | Defines Bicep modules for storage, ACR, AKS, monitoring, identity and RBAC. |
| 09 | `09-azure-sdk` | Moves cloud access onto SDK calls and managed credentials. |
| 10 | `10-python-execution` | Connects source, AST, RISC-V ADD, bytecode, VM, and host architecture. |
| 11 | `11-worker-pool` | Adds bounded workers, IPC queues, shared memory, multicore and NUMA measurements. |
| 12 | `12-echo-service` | Compares TCP streams with UDP datagrams and traces both through Ethernet and IPv4. |
| 13 | `13-packet-tools` | Adds packet inspection helpers for wire debugging. |
| 14 | `14-native-extension` | Compares Python and native framing behavior and takes measurements. |
| 15 | `15-cpython-bytecode` | Adds typed task queries and a tested custom interpreter instruction. |
| 16 | `16-framed-protocol` | Frames traffic through a bounded ring buffer. |
| 17 | `17-wire-format` | Defines versioned serialization for relay messages. |
| 18 | `18-validated-models` | Validates task payloads before work starts. |
| 19 | `19-rpc-service` | Exposes remote task submission as an RPC service. |
| 20 | `20-zeromq-patterns` | Adds real PUB/SUB sockets and Celery background work over Valkey. |
| 21 | `21-websocket-service` | Adds resumable WebSockets and typed GraphQL operations. |
| 22 | `22-logical-clocks` | Normalizes civil time and preserves causal order across replicas. |
| 23 | `23-quorum-basics` | Models quorum reads, writes and partitions. |
| 24 | `24-raft-election` | Simulates terms, votes and leader selection. |
| 25 | `25-replicated-log` | Simulates replication and commitment with retained in-memory state. |
| 26 | `26-distributed-lock` | Separates Redis command atomicity from client races, then adds owner tokens, Lua release and fencing. |
| 27 | `27-stateless-service` | Splits stateless front ends from stored state. |
| 28 | `28-partitioned-store` | Models placement, quorum visibility and concurrent versions. |
| 29 | `29-kv-store` | Models Cosmos-like access, conditional writes and expiry. |
| 30 | `30-dag-engine` | Simulates DAG attempts, concurrency bounds and failure propagation. |
| 31 | `31-airflow-dags` | Defines an Airflow-style blueprint without a running scheduler. |
| 32 | `32-container-deploy` | Adds local and AKS delivery, Nginx, scaling and versioned rollouts. |
| 33 | `33-load-shedding` | Bounds queueing and retries under overload. |
| 34 | `34-instrumented-service` | Adds telemetry, trace propagation, and diagnosis queries. |
| 35 | `35-operational-data-analysis` | Turns observations into statistical summaries and a web report. |
| 36 | `36-on-prem-cloud` | Plans the configurable SigRaft on-prem cloud. |
| 37 | `37-on-prem-cloud-apis` | Reconciles OpenStack, Kubernetes and registry resources through Python APIs. |
| 38 | `38-resource-scheduling` | Models resource admission, placement, reservations and dispatch plans. |
| 39 | `39-sigraft-service` | Assembles REST, GraphQL, optional WebSocket commands and status streams, scheduling, telemetry and versioned release documentation. |

## Quality gates

The map describes independently runnable learning stages. A retained
contract or artifact entry does not mean this process imports that package or
implements every capability demonstrated there.

```bash
pybootstrap check
```

Finish when you can distinguish accepted jobs from executed jobs, interface
results from scheduler plans, local telemetry emission from backend ingestion,
and artifact checks from results gathered on a deployed release. `pybootstrap check`
must exit 0 with all configured gates run. Exit 1 means findings; exit 2
means a gate could not run and supplied no verdict.

Stop the HTTP service and documentation preview with Ctrl-C. Shut down REPL
services to join managed threads and flush providers. Remove
`config.local.toml` and private WebSocket credential files you created.
If you started the shared stack, run `make local-down` from the repository
root; remove volumes only if you intentionally want to discard their data.
Clean up optional cloud resources with their owned deployment's teardown
procedure. Do not publish the example digest or credentials in a release report.
