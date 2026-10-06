# Lab 21 Websocket Service

Development and review guidance for this lab.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means at least one gate
could not produce a verdict. Other gates may still have useful results.
Read the gate output, repair the failed tool or configuration, and rerun.

## Layout

```
src/lab_21_websocket_service/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Sequence IDs are the resume contract. Do not infer replay from wall time.
- Keepalive and reconnect tests use the fake clock, never sleeps.
- Broker fan-out stays behind an abstraction so another backend can replace the
  in-memory broker without changing the session code.
- Keep GraphQL resolvers on the shared relay task contract and enforce read or
  write scope at the operation boundary.
- Pagination must cap `first` and treat cursors as opaque.
- Batch loaders are request-scoped so cache entries cannot cross identities.
- The live transport test uses loopback and an ephemeral port. It must negotiate
  `graphql-transport-ws` and close both client and server.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.
