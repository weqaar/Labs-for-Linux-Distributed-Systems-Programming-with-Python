# Lab 12 Wire Format

Orientation for anyone, human or AI, working in this repository.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Layout

```
src/lab_12_wire_format/    the package
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
  build, which is worse than no gate at all because it looks like coverage.
