# Labs for Linux Distributed Systems Programming with Python

Instructions for humans and coding agents working in this public repository.
Read the `AGENTS.md` inside a lab before changing that lab.

## Purpose

These are the reader-facing labs for *Linux Distributed Systems Programming
with Python*. The book source is private and must not be added here.

The labs are independently runnable stages of one production-grade product,
not unrelated examples. Labs 1 through 38 develop a distributed task service
named `relay`, operated through `relayctl`. Lab 39 assembles those contracts as
the SigRaft REST and GraphQL service, resource scheduler, CLI, operational
report and Azure and on-prem release paths.

Keep the product contract stable:

- task identifiers look like `task-17`
- tasks move through `queued`, `running`, `succeeded` and `failed`
- the HTTP resource is `/tasks`
- network, clock, storage, runtime and cloud dependencies use typed adapters
- tests use deterministic fakes and do not require an Azure subscription

## Required skillset

Changes should be made by someone comfortable with:

- Python 3.10 or newer, packaging and `pyproject.toml`
- pytest, coverage, `unittest.mock`, static typing and Ruff
- Linux processes, sockets, packet tools, ELF and native extensions
- REST, GraphQL, Typer, HTTPX, JSON, Protocol Buffers, gRPC, ZeroMQ and
  WebSockets
- clocks, quorum, Raft, replication, partitioning, locks, resource scheduling
  and backpressure
- Azure and OpenStack Python SDKs, containers, QEMU/KVM, libvirt, LXC and Kata
- Kubernetes, Harbor, Nginx, OpenTelemetry, CI/CD, Ansible and Fabric
- NumPy, pandas, Matplotlib, SciPy, statsmodels and operational statistics

Do not introduce company-owned code, identifiers, endpoints or configuration.
Examples must remain original and use the `relay` vocabulary.

## CQC coding standards

All changes must follow [`CODING_STANDARDS.md`](CODING_STANDARDS.md). A lab's
own `AGENTS.md` adds lab-specific constraints but does not replace the
shared type, object-design, validation, ownership and testing rules.

Every lab is a pybootstrap project. Its `pyproject.toml` is the single source
of truth for dependencies, build metadata and tool configuration.

The CQC quality contract is:

1. `ruff format --check` passes.
2. `ruff check` passes.
3. `pyright` passes without hiding type errors.
4. `pytest` passes and coverage meets the configured floor, normally 80%.
5. A tool that could not run is an error, not a pass.

`pybootstrap check` preserves three outcomes:

| Exit | Meaning |
|---:|---|
| 0 | Every configured gate ran and passed |
| 1 | A gate ran and found a problem |
| 2 | A gate could not run, so nothing was judged |

Exit 2 is more serious than exit 1. Never add `|| true`, broad exception
handlers, skipped checks or success-shaped fallbacks to make a gate green.
The public pybootstrap project is installed by each lab's `dev` extra from
<https://github.com/packetfive/pybootstrap>.

## Working on a lab

```bash
cd labs/NN-lab-name
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

Keep each lab self-contained. A lab may preserve interfaces and vocabulary
from earlier labs, but it must not import another lab.
Cloud, network, subprocess, clock and runtime APIs belong behind injected
boundaries so the default gate stays offline and deterministic.

Test failure paths, timeouts, duplicate delivery, malformed input and partial
state transitions. Do not use wall-clock sleeps when an explicit fake clock or
scripted transport can express the behavior.

## Repository boundaries

Allowed here:

- lab source, tests, deployment examples and reader instructions
- the shared local resource stack
- scripts that validate the public labs

Not allowed here:

- `.tex` book sources, draft prose, cover files or publication artifacts
- credentials, tenant identifiers, private endpoints or paid-resource defaults
- generated virtual environments, caches, coverage output or build products

Run the changed lab's gate first. Run `make labs` before a release that changes
shared guidance, dependencies or more than one checkpoint.
