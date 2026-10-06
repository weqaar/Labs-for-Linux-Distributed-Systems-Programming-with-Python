# Lab 38 Resource Scheduling

This lab decides which node could run a SigRaft job and reserves that node's
capacity before returning a dispatch plan. It models admission, placement,
quotas and lease recovery without running the selected job.

The scheduler accepts requests for CPU cores, memory, GPUs, wall time,
labels, CPU affinity and NUMA locality. Supplied heartbeats report each
node's available resources. The configured leader excludes stale or
incompatible nodes, ranks the rest with stable tie breaking, records a
reservation in memory, then returns a dispatch plan for the chosen node.

The dispatch plan gives a node agent the cgroup name, CPU set, memory maximum,
NUMA node and GPU visibility environment. The normal lab does not mutate host
cgroups or require GPUs. Deterministic inventories test the same policy
offline.

CPU affinity limits which cores may run a process. NUMA locality keeps its
memory near those cores, and a cgroup would enforce resource limits on the
node. This lab returns those settings for a future node agent to apply.

## Goal and activities

Learn why queues should transport a placement decision rather than choose a
machine, and why capacity must be reserved before dispatch. You will alter
inventories and integer time in the supplied model, not run a cluster scheduler.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The runtime uses standard-library dataclasses and collections; dependencies
are in `pyproject.toml`. No GPU, root access, broker or Azure subscription is
needed. Install using the setup below before starting the REPL.

1. Run the placement example and inspect the final reservation event.
2. Run `pytest -q tests/test_lab_38_resource_scheduling.py`. Compare an
   impossible request with a valid request waiting for temporary capacity.
3. Change a node's heartbeat time or labels and predict candidate exclusion.
4. Inspect quota, lease-expiry and non-leader rejection tests. Verify that
   releasing an allocation changes available capacity.

`become_leader` assigns authority in the model; it does not conduct an election.
`ReplicatedStateLog` is a deterministic stand-in, not a durable or replicated
log. Returned cgroup, CPU, NUMA and GPU values are enforcement plans, not host
changes or evidence that a worker executed an action.

One validation gap remains: an explicit CPU affinity is not checked against
the node's CPU inventory when no NUMA filter is selected. A request for CPU
99 can therefore produce that plan on a node reporting only CPUs 0 and 1.
Do not apply such a plan to a host. The model's scheduler states, including
`scheduled` and `cancelled`, are also separate from the four `/tasks` states.

## Getting started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Python REPL debugging session

Inspect a placement without touching the host:

```pycon
>>> import lab_38_resource_scheduling as lab
>>> lab.__name__, lab.__file__
>>> from lab_38_resource_scheduling import NodeInventory, ResourceRequest, ResourceScheduler
>>> scheduler = ResourceScheduler()
>>> scheduler.become_leader("scheduler-a", 1)
>>> scheduler.heartbeat(NodeInventory("node-a", (0, 1, 2, 3), 8192, heartbeat_at=100))
>>> job = scheduler.submit(
...     "task-17",
...     project="research",
...     action="simulate",
...     request=ResourceRequest(cpu_cores=2, memory_mb=1024),
... )
>>> job.state.value
'queued'
>>> plan, = scheduler.schedule(caller_id="scheduler-a", now=100)
>>> (plan.queue, plan.cpu_set, plan.memory_max_bytes)
('sigraft.node.node-a', '0,1', 1073741824)
>>> scheduler.log.entries[-1].event
'reserved'
```

The final two expressions expose the important ordering: the scheduler records
the reservation before returning the node-specific dispatch plan.

## Run the quality gate

```bash
pybootstrap check
```

The lab is complete when the command exits zero. Tests cover invalid and
impossible requests, temporary capacity shortages, leader ownership,
priority/FIFO order, topology-aware placement, quotas, lifecycle transitions
and allocation lease recovery.

This model shows how to reserve a node before dispatching work for the SigRaft
job-orchestration web service. Lab 39 implements a separate scheduler and API,
not an import of this package. Its capabilities must be checked separately.
Finish when you can explain admission, reservation, dispatch and enforcement
as separate steps, with `pybootstrap check` exit 0. Exit 1 means findings;
exit 2 means a gate could not run. Exit Python to discard inventories and
reservations; no real allocation or cloud resource needs teardown.
