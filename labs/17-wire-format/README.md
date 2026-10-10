# Lab 17 Wire Format

Change a task message without breaking a reader that still uses an older
version. The JSON example distinguishes leaving an owner unchanged from
clearing or replacing it. The Protocol Buffers examples exchange task messages
between old and new schema versions.

## Goal and activities

Evolve messages without confusing an omitted value with an explicit removal.
The supplied codecs operate on bytes, not a broker or HTTP service. Their
contribution to the SigRaft job-management web service is compatibility
reasoning; Lab 39 does not import these generated modules.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
JSON uses the standard library; Protocol Buffers uses the declared `protobuf`
runtime. `grpc_tools.protoc` is supplied by the development extra, not a
separately maintained dependency list. No subscription or live service is needed.

1. Install and compare absent, null and nonempty owner values in the REPL.
2. Read both `.proto` files and identify stable and reserved field numbers.
3. Run `pytest -q tests/test_lab_17_wire_format.py`. Trace the unknown owner
   field through an old reader back into a new reader.
4. Make a compatible schema extension, regenerate as below, and add a
   version-skew test. Do not reuse a retired number.

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

## Tests

The lab has two kinds of test. `tests/test_lab_17_wire_format.py` holds the
unit tests. They check each codec function, schema field number and size limit
on its own.

`tests/test_functional.py` holds the functional tests. They drive the public
codec functions `decode_v1`, `decode_v2` and `decode_owner_patch` the way two
relay processes on different schema versions would. They check that a job
message keeps its owner and priority through an old reader, that a sequence of
owner patches sets, keeps and clears the owner, and that bad, oversized or
corrupt messages are rejected.

Run each kind alone with `pytest tests/test_lab_17_wire_format.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Layout

```
src/lab_17_wire_format/    the package
tests/                the test suite
schemas/               old and new .proto contracts
pyproject.toml        dependencies, tool settings and gate definition
```


## Regenerate Protobuf code

The generated modules are checked in so installing the wheel does not require a
compiler. Regenerate them whenever a schema changes:

```bash
python -m grpc_tools.protoc \
  -I schemas \
  --python_out=src/lab_17_wire_format/pb \
  --pyi_out=src/lab_17_wire_format/pb \
  schemas/task_v1.proto schemas/task_v2.proto
```

Review field numbers before accepting generated output. A removed number must
be reserved and must never identify a new field.

The JSON tests keep absent, null and a value as three separate operations. The
Protobuf tests prove that an old reader preserves fields it cannot interpret
when it relays the message.
## Python REPL debugging session

After the editable install, compare JSON field presence with Protobuf decoding:

```pycon
>>> import inspect
>>> import lab_17_wire_format as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> from lab_17_wire_format.json_codec import decode_owner_patch
>>> [decode_owner_patch(body).operation.name for body in
...  (b"{}", b'{"owner": null}', b'{"owner": "reader"}')]
['UNCHANGED', 'CLEAR', 'SET']
>>> from lab_17_wire_format.pb import task_v1_pb2
>>> from lab_17_wire_format.protobuf_codec import decode_v2
>>> task = task_v1_pb2.Task(id="task-17", action="index")
>>> decode_v2(task.SerializeToString()).id
'task-17'
```

The three JSON results are three requested operations, even though the first
two contain no owner string. The Protobuf result shows that a new reader can
recover an old message's task ID. Follow the reverse-direction test next to
see what an old reader does with fields it does not recognize.

Finish when you can explain presence, unknown-field preservation and the
difference between decoding and domain validation. `pybootstrap check` must
exit 0. Review generated diffs and remove only scratch payloads you created.
No sockets, queues or cloud resources are opened by these examples.
