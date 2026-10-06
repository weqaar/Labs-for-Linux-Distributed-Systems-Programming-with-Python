# SigRaft lab coding standards

These standards apply to every lab. A lab's `AGENTS.md` adds constraints for
that lab but does not replace this file. In this file, a checkpoint means one
lab's stage of SigRaft. Use these rules when designing
and reviewing your code. `pybootstrap check` runs the configured automated
checks, but it cannot assess every design decision.

Python executes dynamically. In these labs, application code is also
**statically checked**: type annotations let Pyright check calls, assignments
and return values before you run the program. Pyright must pass without
hiding errors.

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
- Preserve the shared job contract. In code, identifiers look like `task-17`,
  the action is a string, states are `queued`, `running`, `succeeded` and
  `failed`, and the HTTP path is `/tasks`. Reader-facing prose calls these
  jobs. Do not call the Python status object a task record.

## Objects, functions and boundaries

- Use a class when identity, valid state transitions, resource ownership or
  replaceable behavior belongs together.
- Use immutable, slot-backed dataclasses for domain values where that makes
  invalid mutation harder.
- Use `Protocol` to describe replaceable interfaces. Pass clocks, transports,
  stores and cloud clients into the code that needs them rather than
  constructing them inside the job-processing logic.
- Prefer composition to inheritance. Use inheritance only when a derived
  class can be used wherever the base class is expected, and test that
  behaviour.
- Keep transformations and calculations as pure module-level functions when
  object identity or mutable state adds nothing.
- Do not turn every noun into a class or create static-method containers merely
  to claim the code is object-oriented.
- Keep vendor SDK objects inside their adapter. Return typed values defined
  by the lab to the rest of the application, rather than exposing SDK objects.

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
  before publishing changes shared by multiple labs.

## Documentation and dependencies

- Give public modules, classes, functions, methods and properties docstrings
  describing their API contract. State units, constraints, return semantics,
  side effects and relevant exceptions where callers need them. Type hints
  identify accepted types; they do not explain units, side effects or failure
  conditions.
- Follow PEP 257's docstring structure and use Google-style sections for
  arguments, results and exceptions when needed. Comments explain why a
  non-obvious decision is necessary, not what the next statement plainly does.
- Follow the repository's configured Ruff formatting and lint rules. PEP 8
  recommends 79-character code lines; these labs intentionally choose 100 for
  consistency. Automatic formatting does not check every PEP 8 recommendation.
  PEP 484 function annotations and PEP 526 variable annotations support static
  checking, not runtime validation.
- Practise the documentation workflow in `02-package-build/docs/` and
  `03-quality-gate/docs/`, with executable examples in their documentation
  tests. Those labs declare Sphinx locally; other labs do not need a
  documentation dependency merely to follow these writing rules.
- Describe what the supplied code actually implements and what its tests
  check. Distinguish local tests using controlled inputs from optional
  exercises that connect to live services.
- Begin each README with the lab's goal and purpose. Explain the technologies
  used, the steps to perform, expected observations, and what the reader learns.
  State how the lab contributes to SigRaft and whether later labs
  follow the same design or actually import or call this lab's code.
- Introduce examples before asking the reader to run them, then explain their
  results. Give the exercise a finishing condition and cleanup instructions;
  installation followed by a passing gate is not the whole learning activity.
- Write instructions for the learner and API documentation for the caller.
  Replace abstract shorthand with the component, operation and result you
  mean. For example, say that a dictionary stores job-status objects. Do not
  say that it owns the lab's vocabulary. Read the surrounding explanation before editing,
  and preserve the distinction between a simulation and a live service.
- Declare runtime and development dependencies in `pyproject.toml`; do not
  create a second dependency list.
- Prefer the standard library or an existing dependency. Add a package only
  when its functionality would be harder to implement and maintain correctly
  in a small local module.
- Preserve compatibility with the Python version declared by the lab.

## Required gate

From a lab directory:

```bash
pip install -e ".[dev]"
pybootstrap check
```

The required result is exit 0 after format, lint, type, test and any other
configured gates all run. Inspect the summary; a skipped check is not a
completed check. Exit 1 means a gate found a problem. Exit 2 means at least
one gate could not produce a verdict. Other gate results may still be useful,
but you must resolve the error before reporting a successful run.
