# Lab 25 Replicated Log

## Goal and activities

Distinguish an appended command from one committed by a majority and applied
to a state machine. The reference implementation extends election in a
deterministic Python simulation, not a running durable cluster.
Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The standard library supplies the simulation; dependencies are declared in
`pyproject.toml`. No external network or Azure subscription is needed.

The log records commands in order. Committing marks which commands are agreed
for use; applying runs those commands against the job status. The code
calls that status the task state. The state
machine is the code that performs those ordered state changes. Keeping these
steps separate prevents an isolated leader's private entry from changing
visible task state.

1. Install, elect a simulated leader and enqueue one command in the REPL.
2. Run `pytest -q tests/test_lab_25_replicated_log.py` and inspect log index,
   commit index and state-machine history after enqueue, start and completion.
3. Crash a follower in the simulation, submit commands, restart it and catch
   up. Compare its log and applied snapshot with the leader's.
4. Partition the leader away from a majority and inspect the uncommitted
   entry. Heal after a replacement election and verify it is not applied.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

Run the configured checks before considering the lab complete:

```bash
pybootstrap check
```

Add `-v` to include warnings, `--gate lint` to run one gate, and `--fix` to
apply the corrections tools can make on their own.

Each gate can also be run directly, because pybootstrap does not wrap or
reconfigure them:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate ran and found problems |
| 2 | A gate could not run and supplied no verdict |

Fix findings reported by exit 1. For exit 2, repair the tool or its
configuration and rerun it; an unavailable check cannot establish a pass.

The dev extra installs the public pybootstrap project and its gate tools from
GitHub, so a fresh clone receives the same quality runner used by every lab.

## Simulation focus

The package extends election into a replicated job log and deterministic
state machine. Tests cover:

- majority-only commit
- ordered application of committed commands
- follower catch-up after missing the whole write sequence
- restart from retained in-memory log state
- an isolated leader's uncommitted entry never becoming visible

`PersistentNodeState` is a Python object retained by the simulation. There is
no disk persistence, real network replication or crash-recovery storage test.
The state machine's terminal enum is `RelayTaskStatus.COMPLETED`, a name used
only inside this simulation, not the service status `succeeded`. Its commands describe
enqueue/start/complete operations, not execution of an action.

## Layout

```
src/lab_25_replicated_log/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Python REPL debugging session

After the editable install, inspect log entries and node state:

```pycon
>>> import inspect
>>> import lab_25_replicated_log as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> cluster = lab.ReplicatedRelayCluster(("n1", "n2", "n3"))
>>> cluster.trigger_timeout("n1") is lab.NodeRole.LEADER
True
>>> result = cluster.submit_command(
...     "n1", lab.EnqueueTask(task_id="task-17", queue="default", created_tick=1),
... )
>>> result.committed, cluster.node("n1").commit_index
(True, 1)
```

The first command commits at index one because the leader reaches a majority.
Inspect the leader and follower state-machine snapshots to find `task-17`.
Then run the isolated-leader test: an entry can exist in its log while the
task is absent from applied state.

This lab shows when a replicated command may change visible job state
in the SigRaft job-orchestration web service. Lab 39 does not import this log.
Finish when you can explain why an isolated leader cannot make its command
visible, with `pybootstrap check` exit 0. Exit Python to discard all model
state; no external resources require cleanup.
