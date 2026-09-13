# Labs for Linux Distributed Systems Programming with Python

The public labs for the book *Linux Distributed Systems Programming with
Python* by Weqaar Janjua.

The labs are independently runnable stages of one product: `relay`, a
distributed task service operated through the `relayctl` CLI. Each lab teaches
one topic and advances the same product contract. Together they build toward
the runnable SigRaft REST and GraphQL service, resource-aware scheduling,
operational analysis, Azure and reader-managed cloud deployment, release
evidence and rollback in Lab 39.

The book source is not part of this repository.

## Start

Python 3.10 or newer and Git are required.

```bash
python3 -m venv .venv
source .venv/bin/activate
cd labs/01-first-service
pip install -e ".[dev]"
pybootstrap check
```

Every lab has its own README and `pyproject.toml`. A lab is complete only when
`pybootstrap check` exits zero.

Read [`CODING_STANDARDS.md`](CODING_STANDARDS.md) before changing a lab.
It requires statically checked interfaces, runtime validation, appropriate
object-oriented design, injected adapters, explicit failures, bounded
resources and deterministic tests.

## Run all gates

After installing the dependencies for the labs you are checking:

```bash
make labs
```

Local open-source resources and Azure alternatives are documented in
[`local/README.md`](local/README.md). The local stack includes Azurite,
PostgreSQL, Valkey, RabbitMQ, OpenTelemetry, Jaeger, Prometheus, Loki and
Grafana. [`onprem/README.md`](onprem/README.md) explains the configurable
SigRaft on-prem cloud and its complete and workstation profiles.

Licensed under the Apache License 2.0.
