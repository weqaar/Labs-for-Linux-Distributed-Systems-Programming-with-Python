# Lab 12 Echo Service

Orientation for anyone, human or AI, working in this repository.

## Lab role

This lab is the `relay` byte-stream stage. Keep the standalone service
name as `relay`, the future CLI name as `relayctl`, and task records shaped as
`task_id`, `definition`, and `state` with the states `queued`, `running`,
`succeeded`, and `failed`.

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
src/lab_12_echo_service/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
- Packet-journey tests must serialize and dissect frames offline. Do not send
  raw packets, sniff an interface, require root or depend on host addresses.
- Keep Scapy distinct from the Linux datapath: it models protocol bytes for
  inspection and does not emulate `sk_buff`, `net_device`, routing, TCP state,
  offloads or a device driver.
