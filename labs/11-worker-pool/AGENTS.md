# Lab 11 Worker Pool

Development and check instructions for this lab.

## Checkpoint role

This lab is the `relay` thread and process worker checkpoint. Keep the service
name as `relay`, the CLI name as `relayctl`, and job fields named
`task_id`, `definition`, and `state` with the states `queued`, `running`,
`succeeded`, and `failed`.

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
src/lab_11_worker_pool/    the package
tests/                the test suite
```

## Conventions

- Keep multiprocessing targets at module scope and use one explicit `spawn`
  context for processes, queues, locks, managers, and shared state.
- Keep queue payloads bounded and picklable. Do not pass live clients,
  closures, file handles, or application secrets to child processes.
- Retain child process objects, inspect exit codes, join within a deadline, and
  surface missing results as failures.
- Manager proxies are coordination objects. Protect compound updates with the
  manager lock and do not move bulk payloads through proxy calls.
- Shared-memory users must own disjoint slices or synchronize access. Close
  every mapping and unlink the name exactly once in the parent.
- NUMA discovery must be capability-detected and testable with fixture sysfs
  trees. Normal gates must pass on a single-node machine without `numactl`.
- Keep CPU affinity and memory policy distinct. Parent first-touch placement is
  an explicit limitation of the shared-memory demonstration.
- Keep `py-libnuma` optional. Tests inject its interface and must never change
  the test runner's real affinity or memory policy.
- NUMA worker plans must intersect topology with allowed CPUs, rotate across
  nodes before sibling CPUs, and bind before allocating worker-owned pages.
- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, hiding the failed check from the pipeline.
