# Lab 39 SigRaft Service

Development and review guidance for this lab.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means at least one gate
could not produce a verdict. Other gates may still have useful results.
Read the gate output, repair the failed tool or configuration, and rerun.

## Layout

```
src/lab_39_sigraft_service/    the package
tests/                the test suite
```

## Conventions

The release records list capabilities demonstrated by separate labs. Check
their required entries without claiming those packages run inside this
service. The checked-in release JSON is example data, not a fresh test result.

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Release evidence must retain the Copier project contract, ELF and PE targets,
  Python execution and custom interpreter contracts, UTC and logical-time
  contract, bounded multicore and NUMA placement, TCP streams, UDP datagrams
  and bounded retry, offline Scapy Ethernet, IPv4, TCP and UDP encapsulation,
  Celery and Valkey delivery, ZeroMQ, WebSocket and GraphQL APIs, Bicep and AKS
  infrastructure, and the
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
- Release evidence must retain Redis command serialization, client-sequence
  interleaving, Lua script-cache reload, owner-token release and destination
  fencing contracts.
- The runnable service must keep REST and GraphQL on the same task methods.
  GraphQL operation errors remain distinct from HTTP transport failures.
- The optional WebSocket listener shares those methods. Never trust a
  caller-supplied scope, retry a submission after disconnect, or fabricate job
  progress. Snapshot registration and transitions use the same lock.
- Documentation builds and doctests run inside pytest. Keep API docstrings,
  private-token setup, transport limits and release instructions aligned with
  the installed implementation. Do not claim the local preview is a host.
- Resource scheduling must reserve capacity before node-specific dispatch and
  must reject placement changes from a non-leader.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
