# Lab 12 Echo Service

Development and check instructions for this lab.

## Lab role

This lab is the `relay` byte-stream stage. Keep the standalone service
name as `relay`, the future CLI name as `relayctl`, and job fields named
`task_id`, `definition`, and `state` with the states `queued`, `running`,
`succeeded`, and `failed`.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means at least one gate
could not produce a verdict. Other gates may still have useful results.
Inspect the diagnostics, repair the affected check and rerun it before
reporting success.

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
  build, hiding the failed check from the pipeline.
- Packet-journey tests must serialize and dissect TCP and UDP frames offline. Do not send
  raw packets, sniff an interface, require root or depend on host addresses.
- Keep Scapy distinct from the Linux datapath: it models protocol bytes for
  inspection and does not emulate `sk_buff`, `net_device`, routing, TCP state,
  offloads or a device driver.
- UDP loss tests must discard a datagram inside the loopback test server. Do
  not depend on firewall rules, timing races or external packet loss.
