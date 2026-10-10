# Lab 13 Packet Tools

This lab interprets packets that could explain why a connection to
SigRaft failed. It constructs the packets locally, so you can compare replies
without scanning a network.

## Goal and activities

Interpret probe results without confusing a refusal with silence. You will
construct bytes, parse headers and classify responses using the supplied
reference implementation. This teaches connection diagnosis for the SigRaft
job-management web service, not a live network scanner or installed `relayctl`
subcommand. Lab 39 does not import this package.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The runtime uses standard-library byte handling; no Scapy dependency, raw
socket permission or cloud subscription is needed. Dependencies are declared
only in `pyproject.toml`.

1. Install below and inspect `diagnostics.py` and `packets.py`.
2. Build and classify the SYN/ACK fixture in the REPL.
3. Change its flags to RST/ACK and compare the classification. Read the ICMP
   and timeout tests: no packet received is not proof of a filtering rule.
4. Run `pytest -q tests/test_lab_13_packet_tools.py` and retain a test for
   each distinct response category.

## Names this lab keeps

- service name: `relay`
- CLI name: `relayctl`
- task fields: `task_id`, `definition`, `state`
- task states: `queued`, `running`, `succeeded`, `failed`

This lab does not need raw sockets or live cloud networking. It plans
the probe sequence that `relayctl` would use, parses constructed IPv4, TCP,
UDP, and ICMP packets, and distinguishes refused ports, silent timeouts, and
filtered or unreachable paths using deterministic tests.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

Run the configured checks before considering the lab complete:

```bash
pybootstrap check
```

Add `-v` to include warnings, `--gate lint` to run one gate, and `--fix` to
apply the corrections tools can make on their own.

Each gate can also be run directly, because pybootstrap does not wrap or
reconfigure them:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Tests

`tests/test_lab_13_packet_tools.py` holds the unit tests. They check the
packet builders, the parsers and the classifier one function at a time.

`tests/test_functional.py` holds the functional tests. They drive the public
package interface through a whole diagnosis: build the probe plan for job
`task-17`, collect a reply from a fake host for each step, then parse and
classify every reply. They cover a healthy host, a host behind a firewall, a
truncated capture and an invalid probe timeout. The fake host constructs
packets offline, so no raw socket or root permission is needed.

Run each kind alone with `pytest tests/test_lab_13_packet_tools.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate ran and found problems |
| 2 | A gate could not run and supplied no verdict |

Fix findings reported by exit 1. For exit 2, repair the tool or its
configuration and rerun it; an unavailable check cannot establish a pass.

## Layout

```
src/lab_13_packet_tools/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Python REPL debugging session

After the editable install, construct a TCP reply with SYN and ACK flags,
the flags expected when a peer accepts a connection attempt. The parser
recovers those flags and the classifier interprets them:

```pycon
>>> import inspect
>>> import lab_13_packet_tools as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> packet = lab.build_ipv4_tcp_packet(
...     source_port=7000, destination_port=41000,
...     flags=lab.TcpFlag.SYN | lab.TcpFlag.ACK, payload=b"relay",
... )
>>> parsed = lab.parse_network_packet(packet)
>>> parsed.tcp.payload
b'relay'
>>> lab.classify_probe_evidence(parsed) is lab.ProbeOutcome.OPEN
True
```

The payload survives parsing and the reply is classified as open. Replace
SYN with RST to represent a reset and inspect the refused classification.
A timeout has no reply to inspect, so it must remain a separate observation.

Finish when you can separate raw bytes, parsed fields and inferred outcomes,
and `pybootstrap check` exits 0. The tests construct packets but do not send
them. Exit Python to discard fixtures; there is no listener or infrastructure
to tear down.
