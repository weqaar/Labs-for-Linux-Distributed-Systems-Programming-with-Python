# Lab 17 Wire Format

Compare a presence-aware JSON patch with two versions of a Protocol Buffers task
schema. The tests run old-reader/new-writer and new-reader/old-writer pairs.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

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
src/lab_17_wire_format/    the package
tests/                the test suite
schemas/               old and new .proto contracts
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.

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

After the editable install, inspect both serialization boundaries:

```pycon
>>> import inspect
>>> import lab_17_wire_format as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Construct one task, inspect its Python type, encode it, inspect the byte length,
and decode it back before comparing equality.
