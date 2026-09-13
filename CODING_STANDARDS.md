# SigRaft lab coding standards

These standards apply to every lab. A lab's `AGENTS.md` adds checkpoint-specific
constraints but does not replace this file. The rules describe how code is
designed; `pybootstrap check` supplies evidence that the configured automated
rules passed.

Python executes dynamically. In these labs, application code is also
**statically checked**: public interfaces and meaningful internal boundaries
carry type annotations, and Pyright must pass without hiding errors.

## Types and validation

- Annotate public functions, methods, class attributes, protocols and return
  values. Annotate local variables when inference is ambiguous.
- Use precise domain types, enums, dataclasses and protocols instead of passing
  unstructured dictionaries between layers.
- Treat `Any`, unchecked casts and type-ignore comments as boundary exceptions.
  Keep each one narrow and explain why runtime data cannot be typed earlier.
- Validate JSON, environment variables, files, SDK responses and other
  untrusted values at entry. Static types do not validate runtime input.
- Do not use `assert` to validate input or enforce an operational condition;
  optimized Python can remove assertions. Raise a specific exception instead.
- Preserve the shared task contract: IDs such as `task-17`, an action, the
  `queued`, `running`, `succeeded` and `failed` states, and `/tasks`.

## Objects, functions and boundaries

- Use a class when identity, valid state transitions, resource ownership or
  replaceable behavior belongs together.
- Use immutable, slot-backed dataclasses for domain values where that makes
  invalid mutation harder.
- Use `Protocol` for adapter ports. Inject clocks, transports, stores and cloud
  clients rather than constructing them inside domain logic.
- Prefer composition to inheritance. Use inheritance only for a real
  substitutable relationship whose contract is tested.
- Keep transformations and calculations as pure module-level functions when
  object identity or mutable state adds nothing.
- Do not turn every noun into a class or create static-method containers merely
  to claim the code is object-oriented.
- Keep vendor SDK objects inside their adapter. Return project-owned typed
  values to the application layer.

## Failures and resources

- Raise specific exceptions that preserve whether input was invalid, a request
  never arrived, a reply was lost, a deadline expired or an outcome is unknown.
- Do not add broad catches, silent early returns, `|| true`, or
  success-shaped fallbacks. If an optional capability was explicitly requested
  and is unavailable, fail with a useful error.
- Bound input sizes, queues, concurrency, retries, paging, retained history and
  cache growth. Use monotonic deadlines for elapsed-time decisions.
- Give files, sockets, processes, threads, clients and background workers one
  clear owner. Close or join them on success and failure.
- Do not block an async event loop. Use an async API or explicitly offload
  blocking work to a bounded executor.
- Keep credentials, tokens, private endpoints and sensitive payloads out of
  source, URLs, logs, metrics and exceptions.

## Tests and evidence

- Test public behavior and invariants, including malformed input, duplicate
  delivery, timeouts, partial failure and cleanup.
- Use deterministic fakes for network, clock, local-runtime and cloud
  dependencies in the default gate. Do not use timing sleeps when an injected
  fake can represent the event.
- Add a regression test with every defect fix. A test must fail for the broken
  behavior and pass for the corrected behavior.
- Keep tests typed and readable. Do not weaken production types or expose
  internals only to make a test convenient.
- Run the smallest changed lab gate while developing, then run `make labs`
  before publishing changes shared by multiple checkpoints.

## Documentation and dependencies

- Keep the lab README aligned with the capability the code actually proves.
  Distinguish deterministic gate evidence from optional live integration work.
- Declare runtime and development dependencies in `pyproject.toml`; do not
  create a second dependency list.
- Prefer the standard library or an existing dependency. Add a package only
  when it owns a real boundary better than a small local implementation.
- Preserve compatibility with the Python version declared by the lab.

## Required gate

From a lab directory:

```bash
pip install -e ".[dev]"
pybootstrap check
```

The required result is exit 0 after format, lint, type and test gates all run.
Exit 1 means a gate found a problem. Exit 2 means a gate could not run and
therefore produced no verdict; it must never be reported as success.
