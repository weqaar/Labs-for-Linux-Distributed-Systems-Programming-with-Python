# Lab 30 Release Pipeline

This final checkpoint ties the relay task service together. The lab includes a
small runnable REST API, a matching `relayctl` client contract, release
artifacts that prove the same digest reaches staging and production, and offline
checks for rollback, agent convergence, and post-deploy verification.

## Run the compact relay service

```bash
python -m lab_30_release_pipeline.relay_api --port 8081 --digest sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
python -m lab_30_release_pipeline.relayctl metadata --base-url http://127.0.0.1:8081
```

From the repository root, the same final checkpoint runs with the shared local
resource stack:

```bash
make local-check
make local-up
python -m lab_30_release_pipeline.relayctl metadata --base-url http://127.0.0.1:8080
```

The local stack uses open-source storage, database, cache, messaging and
telemetry services. Azure Pipelines and the Azure deployment remain the cloud
release target; local deployment exercises the same service and adapter
contracts without requiring a subscription.

## Release artifacts

- `artifacts/azure-pipelines.yml` gates the exact commit, builds once, promotes
  one digest, verifies each host, and rolls back to the previous digest.
- `artifacts/self_hosted_agents.yml` converges self-hosted agents without shell
  tasks.
- `artifacts/release-evidence.json` records the commit, the passing quality
  gates, and the promoted digests.
- `src/lab_30_release_pipeline/fabric_executor.py` verifies deployed hosts and
  fails the stage when any host is wrong.

## Relay product map across all 30 checkpoints

| Checkpoint | Lab | Relay product contribution |
|---|---|---|
| 01 | `01-first-service` | Starts the first relay process and request loop. |
| 02 | `02-package-build` | Makes the service installable and repeatable to build. |
| 03 | `03-quality-gate` | Adds reproducible lint, type, and test gates. |
| 04 | `04-cli-tool` | Introduces the first `relayctl` command surface. |
| 05 | `05-azure-resources` | Names and provisions the Azure resources relay needs. |
| 06 | `06-azure-sdk` | Moves cloud access onto SDK calls and managed credentials. |
| 07 | `07-worker-pool` | Adds bounded worker concurrency for task handling. |
| 08 | `08-echo-service` | Establishes the request and response socket loop. |
| 09 | `09-packet-tools` | Adds packet inspection helpers for wire debugging. |
| 10 | `10-native-extension` | Speeds a hot path with a native parser. |
| 11 | `11-framed-protocol` | Frames relay traffic so byte streams stay decodable. |
| 12 | `12-wire-format` | Defines versioned serialisation for relay messages. |
| 13 | `13-validated-models` | Validates task payloads before work starts. |
| 14 | `14-rpc-service` | Exposes remote task submission as an RPC service. |
| 15 | `15-zeromq-patterns` | Introduces broker and worker messaging patterns. |
| 16 | `16-websocket-service` | Streams task status changes back to clients. |
| 17 | `17-logical-clocks` | Preserves causal order across replicas. |
| 18 | `18-quorum-basics` | Adds quorum reads and writes for shared state. |
| 19 | `19-raft-election` | Elects a relay leader. |
| 20 | `20-replicated-log` | Replicates the durable task log. |
| 21 | `21-distributed-lock` | Coordinates maintenance with distributed locks. |
| 22 | `22-stateless-service` | Splits stateless front ends from stored state. |
| 23 | `23-partitioned-store` | Shards the store behind relay. |
| 24 | `24-kv-store` | Persists relay state in a key-value layer. |
| 25 | `25-dag-engine` | Executes task dependencies as a DAG. |
| 26 | `26-airflow-dags` | Orchestrates relay flows with scheduler DAGs. |
| 27 | `27-container-deploy` | Selects and secures local VM/container runtimes, then packages relay for rollback by digest. |
| 28 | `28-load-shedding` | Bounds queueing and retries under overload. |
| 29 | `29-instrumented-service` | Adds telemetry, trace propagation, and diagnosis queries. |
| 30 | `30-release-pipeline` | Gates release evidence, promotes one digest, and proves rollback. |

## Quality gates

```bash
pybootstrap check
```
