# Lab 14 Native Extension

This lab compares a pure-Python framing checksum and parser with
a buildable native fast path.

The frame contains a length header followed by task bytes. Both implementations
must recover that length and calculate the same checksum before their running
times can be compared. The checksum detects changes in this exercise; it is
not an authentication mechanism.

## Goal and activities

Establish behavioral equivalence before evaluating performance. You will
inspect the Python reference and C extension, compare frame digests and record
which implementation actually ran. A fallback result is not native evidence.
This shows how to evaluate a C extension before using it to optimize the
SigRaft job-management web service; Lab 39 does not automatically use this extension.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The pure path uses the standard library. Building the extension needs a C
compiler and Python development headers; build configuration and dependencies
are declared in `pyproject.toml`. No network service or subscription is needed
after installation.

1. Install and check `has_native_extension()` in the REPL.
2. Inspect `pure.py`, `native.py` and the C source. Compare checksums for the
   same frame, not timings from different payloads.
3. Run `pytest -q tests/test_lab_14_native_extension.py`. Include truncated
   and oversized inputs as well as valid frames.
4. Use `benchmark_frame_digests` only after equivalence passes. Record its
   implementation field and environment; do not require a speed-up in tests.

## Names this lab keeps

- service name: `relay`
- CLI name: `relayctl`
- task fields: `task_id`, `definition`, `state`
- task states: `queued`, `running`, `succeeded`, `failed`

This lab keeps the pure-Python implementation as the reference and
builds a small native extension from `pyproject.toml` alone. The tests assert
equivalence on a fixed frame corpus and record benchmark measurements without
claiming a speed-up when the native path is unavailable.

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
src/lab_14_native_extension/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Python REPL debugging session

Inspect which implementation and extension were loaded:

```pycon
>>> import inspect
>>> import lab_14_native_extension as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isfunction)
>>> help(lab)
>>> lab.has_native_extension()
>>> task = lab.RelayTask("task-17", "inspect-frame", lab.TaskState.QUEUED)
>>> frame = lab.render_task_frame(task)
>>> implementation = lab.resolve_implementation(prefer_native=False)
>>> digest = lab.describe_frame(frame, implementation=implementation)
>>> digest.implementation, digest.payload_length == len(task.to_payload())
('python', True)
```

The example deliberately selects Python, even if the native module is
available. Its result confirms the decoded length matches the task payload.
Now compare that result with `prefer_native=True`, recording which module
was actually selected. Matching values establish equivalence for this frame,
not portability or a speed-up.

The loader falls back on any `ImportError`, not only a missing extension file.
A failed native import can therefore look like an unavailable extension.
Inspect the native import when a build unexpectedly selects Python; a successful
fallback does not establish that the native artifact is healthy.

Finish when both paths agree where the native module is available, unavailable
native execution is identified explicitly, malformed frames are rejected, and
`pybootstrap check` exits 0. Keep measurement artifacts only as needed; remove
only build outputs you created and deactivate the environment. There are no
cloud resources to destroy.
