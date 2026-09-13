# Lab 21 Websocket Service

This checkpoint adds a relay event stream and typed GraphQL API. The failure
policy uses in-memory fakes, while a separate test opens a real loopback
WebSocket with the `graphql-transport-ws` subprotocol.

- replay from `resume_after` so reconnects lose no events
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

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
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
```

Execute one authorized query in memory, inspect its `data` and `errors`, then
compare that result with the real loopback WebSocket frames.
