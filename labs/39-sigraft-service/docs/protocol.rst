WebSocket command and notification protocol
===========================================

The endpoint is ``/ws`` on the configured WebSocket port, with subprotocol
``sigraft.jobs.v1``. This is not ``graphql-transport-ws``. The earlier WebSocket
lab teaches that GraphQL-specific protocol separately.

Authenticate the handshake with an Authorization bearer token. Each JSON text
message has an ``id`` string of 1 to 64 characters and an ``operation``.
The connection accepts at most 1,024 distinct command IDs; repeated IDs are
rejected rather than executing the command again.

For example, submission uses this envelope::

   {"id":"1","operation":"request","method":"POST","path":"/tasks",
    "body":{"action":"count failed requests"}}

The response carries the same ID, a numeric ``status`` and ``data`` or
``error``. Supported routes are submission and retrieval through ``/tasks``,
``GET /metadata``, and ``POST /graphql``. A server-derived read scope is needed
for retrieval and metadata, and a write scope for submission. GraphQL resolvers
check scopes for their own operations.

Subscribe with ``{"id":"2","operation":"watch","task_id":"task-1"}``.
The server acknowledges before sending the snapshot. Events carry ``id``,
``type: "event"``, ``epoch``, ``sequence`` and ``job``. Send
``{"id":"3","operation":"unwatch"}`` to stop the subscription without closing
the connection. Ordinary commands can use that connection while it is subscribed.
The CLI exposes a single watch; clients with multiple outstanding commands
must match incoming messages to their request IDs.

There is at most one subscription per connection, 32 connections and 32
subscriptions per listener/service respectively. Application mailboxes hold at
most 32 events. Inbound messages are limited to 16 KiB, with a 16-frame receive
high-water mark. Ping and pong detect stalled peers; they do not establish
whether a job ran.

Snapshot and transition publication use the same lock. A change cannot fall
between reading the initial state and registering the observer. Sequence
numbers increase within the service process, but may skip because other jobs
also change. The listener epoch changes when the listener restarts.

Recovery is explicit
--------------------

There is no durable event history and no resume cursor in this final listener.
On disconnect or close code 1013, reopen a watch to obtain a fresh snapshot.
Intermediate changes may have been missed. After a process restart, even the
in-memory job may be gone; reusing an old identifier can refer to different work.

Never automatically retry submission after losing a connection: the service
may have accepted it before the response was lost. Request IDs prevent
re-execution only within that live connection, not across reconnects.
Recovering duplicate submissions or missed events across process restarts
requires durable storage for request outcomes and event history.
