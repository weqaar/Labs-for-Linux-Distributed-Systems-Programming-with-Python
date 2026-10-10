# Lab 09 Azure Sdk

This lab stores task documents in blobs and prepares queue messages
through Azure SDK adapters. An adapter translates the lab's task values into
SDK calls, so application code need not handle SDK-specific objects.

## Goal and working order

Separate storage policy from SDK transport behavior, especially when a write
may have succeeded but its reply was lost. The reference implementation has
real SDK wrappers, but the exercises inject fake clients; it is not an HTTP
service or a worker that consumes and executes queued jobs.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
`azure-core`, `azure-identity`, `azure-storage-blob` and
`azure-storage-queue` are declared runtime dependencies. Local tests need no
subscription or credential. Real endpoints and separately granted blob/queue
data roles are required only if you deliberately compose live clients.

1. Install below and round-trip the task document in the REPL.
2. Read the fake factories in `tests/test_lab_09_azure_sdk.py`, then run
   `pytest -q tests/test_lab_09_azure_sdk.py`.
3. Compare request loss, response loss, authentication failure and permission
   failure. A blob overwrite can be idempotent; queue enqueue is not.
4. Add a fake page to the paging test and verify it is requested lazily.
   Keep the SDK's configured retries separate from application decisions.

## Capability added here

- create relay blob and queue adapters behind typed protocols
- inject a `DefaultAzureCredential` compatible credential instead of using
  connection strings
- inspect how request and response exceptions map to retry flags, then decide
  whether repeating the particular operation can duplicate its effect
- list task documents lazily by page with offline fake SDK clients

The two adapters serve different steps in a job submission. The blob keeps
the task document; the queue notifies a worker that work is available. The
following describes that intended flow, not a command installed by this lab:

```text
relayctl submit --task-id task-17 --action rebuild-search-index
relay writes /tasks/task-17 to blob storage and enqueues task-17 for workers
```

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

Direct commands stay the same locally and in CI:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
## Tests

The lab has two kinds of test. `tests/test_lab_09_azure_sdk.py` holds the
unit tests. They check one adapter method, parser or builder at a time against
fake SDK clients, including the mapping of each Azure exception to a relay
error.

`tests/test_functional.py` holds the functional tests. They build the blob
store and queue dispatcher through `build_blob_task_store` and
`build_queue_task_dispatcher` with fake SDK clients, then submit `task-17`,
move it from `queued` through `running` to `succeeded`, and list jobs page by
page. They also check that a lost enqueue reply is not retryable and that a
missing job and a denied read raise different errors.

Run each kind alone with `pytest tests/test_lab_09_azure_sdk.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Python REPL debugging session

After the editable install, inspect the adapter boundary:

```pycon
>>> import inspect
>>> import lab_09_azure_sdk as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> task = lab.RelayTaskSnapshot("task-17", "index", lab.TaskState.QUEUED)
>>> task.resource_path
'/tasks/task-17'
>>> lab.RelayTaskSnapshot.from_json_bytes(task.to_json_bytes()) == task
True
```

The successful round trip shows that serialization retains the task fields
and resource path. It says nothing about Azure availability. Next compare the
protocol, test fake and Azure adapter signatures, then trace one fake write.

The current queue adapter marks `ServiceRequestError` retryable and
`ServiceResponseError` non-retryable. An exception class alone cannot establish
that no message reached Azure. Before retrying an enqueue with an uncertain
outcome, require duplicate suppression or confirmation that transmission never
occurred. The lab has no such duplicate-suppression mechanism. Blob overwrites
repeat the same document, but also need concurrency protection if other writers
can update it. Errors raised while advancing a blob page can escape without the
translation applied when the pager is first created.

## Contribution and completion

SigRaft's job-management web service needs replaceable cloud adapters.
This lab shows how to translate task values into SDK calls and test those calls
with fake clients; Lab 39 does not import this package or thereby
gain blob persistence. Finish when error categories and paging behavior are
demonstrated, and `pybootstrap check` exits 0. Exit 1 means findings; exit 2
means a gate could not run. Fake data disappears with Python. If you perform
live experiments, close SDK clients and credentials and remove only the
containers, queues and resources created for them.
