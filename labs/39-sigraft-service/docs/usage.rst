Using the service and client
============================

Install the package and developer tools from this lab directory::

   python -m pip install -e ".[dev]"

The default HTTP service is unchanged. Supply the actual release digest when
deploying; the repeated hexadecimal character below identifies only a local
exercise::

   sigraft-service --port 8081 --config config.example.toml \
     --digest sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
   sigraftctl --base-url http://127.0.0.1:8081 submit "count failed requests"
   sigraftctl --base-url http://127.0.0.1:8081 status task-1

Use the identifier returned by submission. ``task-1`` assumes a fresh process.
HTTP status requests return once. They do not maintain a subscription.

Optional WebSocket listener
---------------------------

HTTP and WebSocket use separate listening ports but share the same service
object. This preserves the existing HTTP handler and keeps the optional
connection lifecycle explicit. Create private credential files outside the
checkout::

   umask 077
   python - <<'PY'
   import json
   import secrets
   from pathlib import Path
   directory = Path.home() / ".config" / "sigraft"
   directory.mkdir(parents=True, exist_ok=True)
   token = secrets.token_urlsafe(32)
   (directory / "ws-token").write_text(token + "\n")
   (directory / "ws-credentials.json").write_text(
       json.dumps({token: ["tasks:read", "tasks:write"]})
   )
   PY

The loader rejects group-readable or world-readable files. Re-running this
credential-generation exercise rotates the token; stop existing clients and
restart the listener to apply the replacement.

Stop the earlier server, then start both listeners::

   sigraft-service --port 8081 --config config.example.toml \
     --digest sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
     --websocket-port 8082 \
     --websocket-credentials "$HOME/.config/sigraft/ws-credentials.json"
   sigraftctl --base-url ws://127.0.0.1:8082 \
     --transport websocket --token-file "$HOME/.config/sigraft/ws-token" \
     submit "count failed requests"
   sigraftctl --base-url ws://127.0.0.1:8082 \
     --transport websocket --token-file "$HOME/.config/sigraft/ws-token" \
     status task-1 --watch

The watch command prints newline-delimited JSON: a current snapshot followed
by state changes, each carrying a sequence and listener epoch. It stops on
``succeeded`` or ``failed``; Ctrl+C cancels with exit status 130.
An accepted job remains queued until a trusted executor records a transition.
No timer fabricates progress.

GraphQL queries and mutations also work with ``--transport websocket``.
They use the same resolvers as HTTP. WebSocket permissions come from the server
credential file; the HTTP exercise's caller-supplied ``--scope`` does not grant
permissions to a WebSocket principal.

The listener binds only to loopback and rejects browser Origin headers.
For remote CLI access, place it behind a TLS-authenticated deployment boundary
that forwards the WebSocket upgrade and Authorization header, and use
``wss://``. The client rejects plaintext remote connections. The static lab
tokens do not implement token expiry, tenant ownership or an identity provider.
The HTTP interface still requires its own trusted authentication boundary.

Investigating real state notifications
--------------------------------------

In a Python REPL, start a service and the optional listener in the same process::

   from pathlib import Path
   from lab_39_sigraft_service.sigraft_service import SigRaftService
   from lab_39_sigraft_service.websocket_transport import (
       load_credentials, run_websocket_server,
   )
   service = SigRaftService("sha256:" + "a" * 64)
   credentials = load_credentials(
       Path.home() / ".config/sigraft/ws-credentials.json"
   )
   listener = run_websocket_server(service, credentials=credentials, port=8082)
   job = service.submit_task("count failed requests")
   print(job.task_id)

Connect the CLI watch command from another terminal. Back in the REPL, record
``service.transition_task(job.task_id, "running")``, then
``service.transition_task(job.task_id, "succeeded")``. Observe both events.
This exercise manually reports executor states; it does not perform the
requested log calculation. Inspect the method's docstring with ``help`` and
explain why clients cannot invoke it as a network command.

Always clean up the listener before the service::

   listener.close()
   service.shutdown()

Stop CLI and preview processes when finished. Remove only the credential files
created for this exercise if they are no longer needed.
