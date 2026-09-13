# Lab 39 SigRaft Service

Orientation for anyone, human or AI, working in this repository.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Layout

```
src/lab_39_sigraft_service/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Release evidence must retain the Copier project contract, ELF and PE targets,
  Python execution and custom interpreter contracts, UTC and logical-time
  contract, bounded multicore and NUMA placement, offline Scapy Ethernet,
  IPv4 and TCP encapsulation, Celery and Valkey delivery, ZeroMQ, WebSocket and
  GraphQL APIs, Bicep and AKS infrastructure, and the
  Nginx, scaling and progressive Kubernetes delivery evidence from earlier
  relay checkpoints, plus OpenTelemetry and the SigRaft on-prem cloud.
- Release evidence must retain the NumPy, pandas, Matplotlib, SciPy and
  statsmodels analysis contracts and the transactional TOML reload contract.
- Release evidence must retain the bounded Redfish client and read-only
  data-centre inventory endpoint. Do not add BMC control actions.
- Release evidence must retain PCIe and NUMA accelerator placement, IOMMU
  grouping, GPU-NIC transfer planning and explicit RoCE congestion policy.
- Release evidence must retain the FastAPI ASGI boundary, Uvicorn factory and
  optional uvloop contract, including bounded metadata and blocking-loop tests.
- Release evidence must retain bounded Ray submission, atomic in-flight
  deduplication, fingerprint conflicts, resource validation and the default
  usage-statistics opt-out. Do not describe the actor ledger as durable.
- The runnable service must keep REST and GraphQL on the same task methods.
  GraphQL operation errors remain distinct from HTTP transport failures.
- Resource scheduling must reserve capacity before node-specific dispatch and
  must reject placement changes from a non-leader.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
