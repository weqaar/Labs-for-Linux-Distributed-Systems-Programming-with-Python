# Labs for Linux Distributed Systems Programming with Python

The public labs for the book *Linux Distributed Systems Programming with
Python* by Weqaar Janjua.

The labs are independently runnable stages of a Python web service for job
management and resource-aware scheduling. Early stages call it `relay`,
operated through the `relayctl` CLI. Each lab teaches
one topic and advances the same product contract. Together they build toward
the runnable SigRaft REST and GraphQL service, resource-aware scheduling,
operational analysis, Azure and reader-managed cloud deployment, release
evidence and rollback in Lab 39.

The book source is not part of this repository.

## Service transports and Python documentation

[Lab 39](labs/39-sigraft-service/README.md) keeps HTTP as the default and adds
an optional authenticated WebSocket listener. Use `--transport websocket` for
commands and `status <job-id> --watch` for a current snapshot followed by state
changes. The lab documents private-token setup and separate listener ports.
These notifications do not execute submitted work or retrieve output files;
the compact service still keeps its job records in memory.

[Lab 2](labs/02-package-build/README.md) introduces Sphinx API documentation,
theme templates, executable examples and a runnable MkDocs comparison.
[Lab 3](labs/03-quality-gate/README.md) makes documentation failures part of
the quality gate and connects PEP 8, PEP 257 and type annotations to the
configured tools. [Lab 21](labs/21-websocket-service/README.md) documents and
executes a streaming-contract example. Lab 39 assembles a versioned service
guide and documentation release artifacts.

Each documentation lab includes strict build, doctest and local preview
instructions. Generated sites remain local build products, not committed
source.

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
