# Lab 21 Websocket Service

This lab adds an event stream and a typed GraphQL API to SigRaft. The Python
import name is `relay`. That name does not mean the program relays traffic.
Tests of reconnect and queue failure use in-memory substitutes, while a separate
test opens a real loopback
WebSocket with the `graphql-transport-ws` subprotocol.

A WebSocket keeps a two-way connection open. GraphQL describes operations:
queries read data, mutations change it and subscriptions receive events.
They are separate layers, so the lab first tests operations in memory and then
tests how a WebSocket carries them.

- replay from `resume_after` while the in-memory broker retains those events
- keepalive pings before idle infrastructure drops the socket
- bounded outbound queue with a clear slow-client close
- broker fan-out so one publish reaches connections on other instances
- reconnect backoff with deterministic jitter so clients do not bunch up
- GraphQL queries and mutations over the shared `/tasks` concepts
- scope-based field authorization and stable client error messages
- bounded cursor pagination and a request-scoped batch loader
- subscription replay from an event sequence
- `connection_init`, `connection_ack`, `subscribe`, `next` and `complete`
  messages over a real WebSocket

The GraphQL service is intentionally small but complete enough to show where
validation, authorization, pagination, batching and schema evolution belong.
It does not replace the REST API or give resolvers permission to bypass the
domain service.

Keep the stream model separate from the GraphQL subscription implementation.
`taskEvents(after: ...)` iterates retained records and then ends; it does not
wait for future events. Its returned `Task` fields omit the event sequence,
so a client cannot derive its next resume position from those results alone.
The cursor on a paginated task query is not a subscription resume cursor.

## Goal and activities

Learn how a reconnecting client requests retained events and how GraphQL
resolvers check permission before reading or changing tasks. Start from supplied
stream models and a schema, not a production replay service. Restarting a session
with retained broker history differs from losing the broker process and its history.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The declared runtime libraries are graphql-core and websockets. No external
broker or Azure subscription is needed; the transport test requires loopback.

1. Install, then run the in-memory GraphQL example below with and without scope.
2. Run `pytest -q tests/test_lab_21_websocket_service.py`. Follow a resume
   cursor, a full outbound queue and an expired token through the fake clock.
3. Compare those policy tests with the real loopback WebSocket handshake.
4. Extend a reconnect test or docstring as below. Do not turn reconnect into
   automatic replay of a mutating client command.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

The same checks can be run one by one:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
## Python REPL debugging session

Inspect the schema and stream objects before opening a connection:

```pycon
>>> import inspect
>>> import lab_21_websocket_service as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab.RelayGraphQL)
>>> api = lab.RelayGraphQL()
>>> result = api.execute(
...     'mutation { submitTask(id: "task-17", action: "index") { id state } }',
...     scopes=frozenset({"tasks:write"}),
... )
>>> result.data
{'submitTask': {'id': 'task-17', 'state': 'QUEUED'}}
>>> result.errors is None
True
```

The mutation returns the fields requested by the client and no operation
errors. `tasks:write` is the permission scope supplied for this call. Remove
it to inspect the authorization error, then compare these in-memory results
with the real loopback WebSocket frames.

## Documenting a streaming contract

A reconnecting client needs to know whether its cursor still identifies
retained events and what happens when its queue fills. State whether a missing cursor fails and whether a full queue closes the
connection, rather than merely listing method arguments.

Inspect `help(lab.ReconnectPolicy.delay_ms)` and execute its examples. Extend
one session method's docstring to identify the units, cursor meaning, expiry
policy and cleanup responsibility. Explain why a reconnect delay does not grant
permission to repeat a submitted job. Add an inline comment only where a
non-obvious invariant needs explanation.

The gate executes the reconnect-policy doctest. Change its expected cap in a
temporary copy and confirm that the example fails, then restore it. The
finishing condition remains `pybootstrap check` exiting zero.

Lab 39 applies these concepts to optional CLI WebSocket commands and status
watching. Its `sigraft.jobs.v1` protocol shares the live service methods with
HTTP but uses fresh snapshots after reconnect, not this lab's retained-history
replay model. Do not infer replay or worker execution from an open socket.

This lab teaches stream recovery and permissions for the SigRaft
job-orchestration web service; its package is not imported into the final process.
Finish when cursor, queue and permission failures are observable, and
`pybootstrap check` exits 0. Exit 1 means findings; exit 2 means a gate
could not run. Close both ends of live connections and join the server as
the loopback test does. Discarding the broker also discards its replay history.
