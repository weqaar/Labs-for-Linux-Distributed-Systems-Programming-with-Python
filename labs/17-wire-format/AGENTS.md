# Lab 17 Wire Format

Development and check instructions for this lab.

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
src/lab_17_wire_format/    the package
tests/                the test suite
schemas/               versioned Protocol Buffers contracts
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Never reuse a Protocol Buffers field number. Reserve removed names and
  numbers in the schema.
- Regenerate checked-in `*_pb2.py` modules with the command in README.md after
  changing a schema.
- JSON absent, null and value are separate domain operations. Tests must keep
  all three distinct.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, hiding the failed check from the pipeline.
