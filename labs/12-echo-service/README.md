# Lab 12 Echo Service

This lab puts relay bytes through a real TCP stream on loopback and uses Scapy
offline to show how one reader-supplied chat message becomes a TCP segment, an
IPv4 packet and an Ethernet frame, then returns to application bytes.

## Lab role

- service name: `relay`
- CLI name: `relayctl`
- task fields: `task_id`, `definition`, `state`
- task states: `queued`, `running`, `succeeded`, `failed`

This lab keeps the payload textual and concentrates on the transport.
The server and client both set explicit socket timeouts, the server shuts down
cleanly, and the tests prove that TCP reads do not preserve write boundaries.
The packet journey serializes and dissects bytes in memory. It does not send a
raw frame, sniff an interface or require root.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Trace encapsulation and decapsulation

Pass any non-empty UTF-8 text up to 1,024 encoded bytes:

```bash
relay-packet-journey "hello from the reader" --view encapsulation
relay-packet-journey "hello from the reader" --view decapsulation
relay-packet-journey "hello from the reader" --view combined
```

The transmit view is top down:

1. application message
2. TCP segment
3. IPv4 packet
4. Ethernet frame

The receive view is bottom up and removes those headers in reverse order. Each
line reports the protocol data unit, total bytes, header bytes and important
addresses or TCP fields. The following line prints the complete hexadecimal
bytes at that stage. The combined view places the serialized wire frame between
the transmit and receive traces.

Scapy models protocol bytes. It does not emulate Linux 6.19 `sk_buff`,
`net_device`, routing, neighbour discovery, TCP state, queueing, checksum
offloads, segmentation, DMA or a NIC. The real `EchoServer` and `EchoClient`
exercise the kernel's loopback TCP path with the same application bytes.

## Quality gates

The same command runs on a laptop and in CI, so a failure is always
reproducible:

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

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate ran and found problems |
| 2 | A gate could not run, so nothing was checked |

The split between 1 and 2 is the point. A missing or misconfigured tool is not
the same as clean code, and a pipeline that treats them alike will eventually
report success while checking nothing.

## Layout

```
src/lab_12_echo_service/    the package
tests/                the test suite
ci/                   pipeline definition
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.
## Python REPL debugging session

After the editable install, inspect both the socket service and packet model:

```pycon
>>> from lab_12_echo_service import trace_chat_message
>>> journey = trace_chat_message("inspect this")
>>> [stage.unit for stage in journey.encapsulation]
['message', 'TCP segment', 'IPv4 packet', 'Ethernet frame']
>>> [stage.total_bytes for stage in journey.encapsulation]
[12, 32, 52, 66]
>>> journey.decapsulation[-1].summary
"'inspect this'"
>>> print(journey.render("decapsulation"))
```

This path opens no socket. Use the loopback tests when debugging live
`send`/`recv` behaviour and the packet journey when debugging layer fields or
serialized bytes.
