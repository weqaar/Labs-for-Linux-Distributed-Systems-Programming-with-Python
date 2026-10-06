# Lab 16 Framed Protocol

This lab recovers complete messages from a byte stream. Each message
has a length header, so the reader can tell whether the bytes received so far
contain a full message or only part of one.

## Goal and activities

Recover messages from arbitrarily split byte chunks while limiting retained
incomplete data. You will inspect the supplied ring buffer, split a
frame, test wraparound and verify partial writes.
This contributes framing rules to the SigRaft job-management web service,
not a listening server or a package imported by Lab 39.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Only standard-library byte operations are used at runtime. Gate dependencies
are declared in `pyproject.toml`; neither Azure nor a real socket is required.

1. Install and trace the REPL frame through two chunks.
2. Read `BoundedRingBuffer` and the reader's capacity check. An incomplete
   frame must fit; a large input chunk can contain many smaller complete frames.
3. Run `pytest -q tests/test_lab_16_framed_protocol.py`. Inspect the fake
   partial sender and the test that feeds blob-like chunks.
4. Add a split at a different header byte and a premature EOF assertion.
   Preserve atomic overflow rejection rather than silently discarding bytes.

## Names this lab keeps

- service name: `relay`
- CLI name: `relayctl`
- task fields: `task_id`, `definition`, `state`
- task states: `queued`, `running`, `succeeded`, `failed`

This lab adds a 4-byte big-endian length prefix, a maximum frame size,
an incremental reader that survives arbitrary chunk boundaries, a partial-write
sender, and reuse of the same reader logic for blob chunks as well as sockets.
The reader now stores incomplete data in a fixed-capacity ring buffer. Tests
force head and tail wraparound, reject overflow atomically, and drain many
frames from an input chunk larger than the ring.
The returned list of complete frames can still grow with the input chunk; the
ring's capacity bounds only data waiting to form a complete frame.

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
src/lab_16_framed_protocol/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Python REPL debugging session

After the editable install, inspect the framing objects:

```pycon
>>> import inspect
>>> import lab_16_framed_protocol as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> payload = b"task-17"
>>> frame = lab.encode_frame(payload)
>>> lab.decode_frame(frame) == payload
True
>>> inspect.signature(lab.IncrementalFrameReader)
>>> reader = lab.IncrementalFrameReader()
>>> reader.feed(frame[:2])
[]
>>> reader.feed(frame[2:])
[b'task-17']
>>> reader.finish()
[]
```

The first feed supplies only half the header, so no payload is returned.
The second completes the frame and returns its payload once. The empty result
from `finish()` means no incomplete bytes remain. Compare that with a test
that finishes after the first feed and must report premature end of input.

Finish when you can explain the length prefix, incomplete data, bounded
capacity and EOF errors, with `pybootstrap check` exit 0. Byte fixtures need
no cleanup beyond exiting Python; close any socket or blob client added in an
optional experiment.
