# Lab 12 Echo Service

## Goal and purpose

This lab implements the low-level network transport foundations for SigRaft.
The package import name is `relay`. (This name is an internal project moniker;
the program does not relay network traffic.)

The lab introduces how network protocols exchange job data across sockets:
1. **TCP stream sockets:** TCP provides an ordered, reliable byte stream.
   Because TCP does not preserve write boundaries, an application cannot
   assume that one `send()` corresponds to one `recv()`. The lab tests
   reading fragmented chunks and reconstructing the original payload.
2. **UDP datagram sockets:** UDP sends independent datagrams that preserve
   message boundaries, but does not guarantee delivery or ordering. The lab
   tests deliberate packet drops and verifies client-side retry logic.
3. **Packet encapsulation and decapsulation:** Using Scapy offline,
   the lab constructs and inspects each protocol layer (Application payload,
   Transport TCP/UDP headers, Internet IPv4 packet, and Link Ethernet frame).

## Learning outcomes

By completing this lab, you will understand:
- Why stream protocols (TCP) require framing and cannot rely on read boundaries.
- Why datagram protocols (UDP) require application-level timeouts and retries.
- How the Linux network stack encapsulates application data into packets.
- How to write socket-based servers with clean shutdown and timeout handling.

## Prerequisites and setup

1. Install below and run the packet journey with a short message.
2. Compare the TCP and UDP header sizes in the REPL.
3. Run `pytest -q tests/test_lab_12_echo_service.py tests/test_packet_journey.py`.
   Inspect the split-read test and the deliberately discarded first datagram.
4. Change a test payload, keeping it within the encoded size limit, and
   confirm that encapsulation and decapsulation preserve its bytes.

## Lab role

- service name: `relay`
- CLI name: `relayctl`
- task fields: `task_id`, `definition`, `state`
- task states: `queued`, `running`, `succeeded`, `failed`

This lab keeps the payload textual so it is easy to compare sent and received bytes.
The TCP tests demonstrate that stream reads do not preserve write boundaries. The
UDP tests preserve a datagram boundary, deliberately discard the first request
and check that a bounded application retry transmits a second copy. Both paths
set explicit timeouts and shut down cleanly. The packet journey serializes and
dissects bytes in memory. It does not send a raw frame, sniff an interface or
require root.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Trace encapsulation and decapsulation

Encapsulation adds protocol headers around application bytes; decapsulation
reads those headers and recovers the payload. A short text message makes the
payload easy to recognize at every layer. Pass any non-empty UTF-8 text up to
1,024 encoded bytes:

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
src/lab_12_echo_service/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Tests

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

`tests/test_lab_12_echo_service.py` and `tests/test_packet_journey.py` hold
both kinds. The unit tests check the task wire format, argument validation and
the offline packet model built with Scapy. The functional tests start the TCP
or UDP echo server on 127.0.0.1 with port 0 and talk to it with the real
client, or run the `relay-packet-journey` entry point. They are
`test_echo_server_round_trips_bytes_over_loopback`,
`test_reads_do_not_preserve_write_boundaries`,
`test_server_shutdown_is_clean_and_stops_accepting_connections`,
`test_udp_echo_preserves_one_datagram_boundary`,
`test_udp_reliability_requires_an_application_retry_policy`,
`test_echoed_application_bytes_match_the_packet_trace` and
`test_cli_prints_requested_view`.

```bash
pytest tests -k "not (echo_server or write_boundaries or shutdown or udp_echo or udp_reliability or echoed or cli_prints)"
pytest tests -k "echo_server or write_boundaries or shutdown or udp_echo or udp_reliability or echoed or cli_prints"
```

`pybootstrap check` runs both kinds of test in its test gate.

## Python REPL debugging session

After the editable install, inspect both the socket service and packet model:

```pycon
>>> import lab_12_echo_service as lab
>>> lab.__name__, lab.__file__
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

The sizes grow as each header is added, and the final summary recovers the
original text. This path opens no socket. Use the loopback tests when debugging live TCP
`send`/`recv` or UDP `sendto`/`recvfrom` behavior, and use the packet journey
when debugging layer fields or serialized bytes.

Finish when you can explain why one TCP read need not equal one write, why UDP
retry belongs to the application, and why offline frames do not prove kernel
behavior. `pybootstrap check` must exit 0. Tests close and join their loopback
servers; close any server you create in your own experiment before exiting.
