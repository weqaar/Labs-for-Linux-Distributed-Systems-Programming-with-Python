# Labs for Linux Distributed Systems Programming with Python

The public labs for the book *Linux Distributed Systems Programming with
Python* by Weqaar Janjua.

The thirty labs are independently runnable checkpoints of one product:
`relay`, a distributed task service operated through the `relayctl` CLI. Each
lab teaches one topic and advances the same product contract. Together they
build toward the runnable REST service, cloud deployment, observability,
release evidence and rollback in Lab 30.

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

## Run all gates

After installing the dependencies for the labs you are checking:

```bash
make labs
```

Local open-source resources and Azure alternatives are documented in
[`local/README.md`](local/README.md). The local stack includes Azurite,
PostgreSQL, Valkey, RabbitMQ, OpenTelemetry and Jaeger.

Licensed under the Apache License 2.0.
