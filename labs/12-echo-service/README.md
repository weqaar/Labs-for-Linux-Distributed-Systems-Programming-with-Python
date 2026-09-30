# Lab 12 Echo Service

This lab puts relay bytes through real TCP and UDP sockets on loopback and uses
Scapy offline to show how one reader-supplied chat message becomes a TCP
segment or UDP datagram, an IPv4 packet and an Ethernet frame, then returns to
application bytes.

## Lab role

- service name: `relay`
- CLI name: `relayctl`
- task fields: `task_id`, `definition`, `state`
- task states: `queued`, `running`, `succeeded`, `failed`

This lab keeps the payload textual and concentrates on transport contracts.
The TCP tests prove that stream reads do not preserve write boundaries. The
UDP tests preserve a datagram boundary, deliberately discard the first request
and prove that a bounded application retry transmits a second copy. Both paths
set explicit timeouts and shut down cleanly. The packet journey serializes and
dissects bytes in memory. It does not send a raw frame, sniff an interface or
require root.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Trace encapsulation and decapsulation

Pass any non-empty UTF-8 text up to 1,024 encoded bytes:

```bash
relay-packet-journey "hello from the reader" --view encapsulation
relay-packet-journey "Hello SigRaft" --transport tcp --view combined
relay-packet-journey "Hello SigRaft" --transport udp --view combined
```

The transmit view is top down:

1. application message
2. TCP segment or UDP datagram
3. IPv4 packet
4. Ethernet frame

The receive view is bottom up and removes those headers in reverse order. Each
line reports the protocol data unit, total bytes, header bytes and important
addresses or transport fields. TCP reports sequence and flags with a minimum
20-byte header. UDP reports length and checksum with an 8-byte header. The
following line prints the complete hexadecimal bytes at that stage. The
combined view places the serialized wire frame between the transmit and receive
traces.

Scapy models protocol bytes. It does not emulate Linux 6.19 `sk_buff`,
`net_device`, routing, neighbour discovery, TCP state, queueing, checksum
offloads, segmentation, DMA or a NIC. The real `EchoServer` and `EchoClient`
exercise the kernel's loopback TCP path. `DatagramEchoServer` and
`DatagramEchoClient` exercise UDP and make retry ownership visible. Retrying an
echo is safe; a protocol with side effects also needs request identifiers and
duplicate suppression.

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
>>> udp = trace_chat_message("Hello SigRaft", transport="udp")
>>> udp.encapsulation[1].unit
'UDP datagram'
>>> udp.encapsulation[1].header_bytes
8
>>> print(journey.render("decapsulation"))
```

This path opens no socket. Use the loopback tests when debugging live TCP
`send`/`recv` or UDP `sendto`/`recvfrom` behavior, and use the packet journey
when debugging layer fields or serialized bytes.
