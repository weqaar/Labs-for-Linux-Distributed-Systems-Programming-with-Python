# Lab 38 Resource Scheduling

This lab adds the resource scheduler that connects SigRaft's task model,
worker queues, Raft leadership and Linux topology controls. It is deliberately
smaller than Slurm, Kubernetes or Ray, but implements the mechanisms they make
visible: admission, deterministic placement, atomic reservations, dispatch,
enforcement plans, quotas and lease recovery.

The scheduler accepts typed requests for CPU cores, memory, GPUs, wall time,
labels, CPU affinity and NUMA locality. Execution nodes report bounded
inventory through heartbeats. The elected scheduler filters incompatible or
stale nodes, scores the remaining nodes with stable tie breaking, commits a
reservation to a replicated-state adapter and only then emits a node-specific
dispatch plan.

The dispatch plan gives a node agent the cgroup name, CPU set, memory maximum,
NUMA node and GPU visibility environment. The normal lab does not mutate host
cgroups or require GPUs. Deterministic inventories test the same policy
offline.

## Python REPL debugging session

Inspect a placement without touching the host:

```pycon
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
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

The lab is complete when the command exits zero. Tests cover invalid and
impossible requests, temporary capacity shortages, leader ownership,
priority/FIFO order, topology-aware placement, quotas, lifecycle transitions
and allocation lease recovery.
