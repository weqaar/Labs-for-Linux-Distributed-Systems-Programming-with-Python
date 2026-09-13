# Lab 11 Thread and process workers with NUMA and accelerator-aware placement

This checkpoint gives `relay` two bounded execution paths. Threads lease
I/O-oriented tasks from a visibility queue and drain on shutdown. Spawned
processes execute CPU-oriented work in separate interpreters, communicate
through queues, share selected coordination state through a manager, and read
bulk bytes from shared memory.

The NUMA exercise inspects Linux topology, balances process workers across
allowed CPUs and nodes, and tests CPU and memory-policy adapters without
assuming the test machine has more than one NUMA node. The accelerator
exercise extends that same sysfs discipline to PCIe GPUs and RDMA-capable
network adapters: parsing bus topology, checking IOMMU isolation groups,
planning peer-to-peer or host-staged transfers and checking a RoCE fabric
against a site's own congestion-control policy, all against fixture data
rather than real hardware.

## Set up the checkpoint

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

## Exercise 1: Preserve the visibility lease

Start with `worker_pool.py` and `queue.py`. Submit queued `RelayTask` objects,
cap the number of handlers in flight, and represent SIGTERM by calling
`request_shutdown`.

The pool must:

1. Stop leasing new work after shutdown is requested.
2. Let current handlers finish.
3. Acknowledge successful and failed work.
4. Leave unacknowledged work available after its visibility timeout.
5. Join every worker thread within the caller's deadline.

The in-memory queue is a deterministic implementation of the same typed lease
contract used later by an Azure queue adapter.

## Exercise 2: Send CPU work to spawned processes

Read `multicore.py`, then run:

```python
from lab_11_worker_pool import CpuWork, run_process_queue

run = run_process_queue(
    (
        CpuWork("task-10", 10),
        CpuWork("task-11", 11),
        CpuWork("task-12", 12),
    ),
    workers=2,
)
print(run.started_worker_pids)
print(run.results)
```

`CpuWork` and `CpuResult` are frozen, picklable message contracts. The parent
creates a bounded input queue and a result queue from the same `spawn`
context. Each child has its own interpreter, GIL, PID, and address space.

Inspect the serial `cpu_transform` result and compare it with every child
result. A result PID must belong to the workers started by the parent and must
not equal the parent PID.

## Exercise 3: Compare IPC primitives

Modify a local copy of the example to use:

| Primitive | Observation |
|---|---|
| `Pipe(duplex=False)` | One producer and one consumer own the endpoints. |
| `Queue(maxsize=N)` | Multiple producers and consumers share bounded admission. |
| `SimpleQueue` | The API is smaller, but no queue bound protects memory. |
| `JoinableQueue` | `join()` completes only after one `task_done()` per item. |

Measure complete elapsed time, serialization time, and payload size. Do not
use `qsize()` or `empty()` to decide whether processing is complete because
another process can change the answer immediately.

## Exercise 4: Use a manager for coordination

Call `count_deliveries_with_manager` with repeated task identifiers. A manager
server owns the dictionary and lock; child processes hold proxies. The lock
covers the complete read-modify-write operation.

This is coordination, not a high-rate data path. Remove the lock and explain
why two proxy method calls are not one atomic increment. Compare manager
updates with returning per-worker dictionaries and reducing them in the
parent.

## Exercise 5: Separate payload from control

`sum_shared_bytes` creates one named shared-memory segment, divides it into
disjoint slices, and sends only the segment name and offsets to workers. Verify
that:

- every byte belongs to exactly one slice
- the parallel sum equals `sum(payload)`
- each mapping is closed
- the parent unlinks the shared-memory name once

Shared memory avoids sending the complete payload through every queue. It does
not supply locks, record validity, byte order, or recovery after a partial
write.

## Exercise 6: Inspect multicore capacity

On Linux, compare:

```bash
lscpu -e=CPU,NODE,SOCKET,CORE,ONLINE
grep -E 'Cpus_allowed_list|Mems_allowed_list' /proc/self/status
python -c 'import os; print(sorted(os.sched_getaffinity(0)))'
```

The allowed affinity set is a better worker-count input than the host CPU count
inside a cpuset-constrained container. Logical CPUs can be simultaneous
multithreading siblings, so two logical CPUs do not always provide two times
the CPU throughput.

## Exercise 7: Read NUMA topology

`discover_numa_topology` reads:

```text
/sys/devices/system/node/node*/cpulist
/sys/devices/system/node/node*/distance
```

`parse_numa_maps` summarizes `N0=pages`, `N1=pages`, and similar fields from
`/proc/PID/numa_maps`. The tests use fixtures so they pass on single-node
machines.

On an approved multi-node host, compare:

```bash
numactl --hardware
numactl --cpunodebind=0 --membind=0 python -m YOUR_BENCHMARK
numactl --cpunodebind=1 --membind=1 python -m YOUR_BENCHMARK
numastat -p PID
```

Allocate and initialize the payload after applying the placement policy.
Linux commonly places anonymous pages on the node whose CPU first writes them.
CPU affinity alone does not select the memory node.

## Exercise 8: Balance and bind process workers

`plan_numa_workers` intersects discovered node CPUs with the process's allowed
affinity set. It rotates workers across nodes first, then across the CPUs within
each node. Apply a placement inside a spawned child before allocating its large
working set:

```python
from lab_11_worker_pool import (
    bind_numa_worker,
    pin_current_process,
)

pin_current_process(cpu_id)
bind_numa_worker(node_id)
payload = bytearray(payload_size)
payload[:] = source
```

Install the optional binding only for the live Linux exercise:

```bash
sudo apt install libnuma1
pip install -e ".[numa]"
```

The pinned dependency is `py-libnuma==1.2`, imported as `numa`. Its
`schedule.run_on_nodes()` call selects CPUs in a NUMA node and
`memory.set_local_alloc()` applies local policy to later allocations. The
normal gate injects a fake binding, so it neither changes test-runner affinity
nor depends on host topology.

Benchmark local and remote placement with a sequential buffer, a fixed stride,
and random offsets. Record elapsed time, throughput, affinity,
`/proc/PID/numa_maps`, `numastat -p PID`, and permitted `perf stat` cache
counters. Recreate and initialize the buffer after every policy change.

## Exercise 9: Plan accelerator topology without a GPU

`accelerators.py` extends the same sysfs discipline from Exercise 7 to PCI
devices generally. `PciFunction` is the general record produced by walking
`/sys/devices`: a bus address, a NUMA affinity hint, a class code and the
chain of PCIe bridge ancestors above it. `AcceleratorDevice` names that same
record once its class code has been confirmed to be a GPU or dedicated
processing accelerator; a network adapter discovered the same way is a
`PciFunction` with a network-controller class code, never an
`AcceleratorDevice`, because it was never filtered as one.
`discover_accelerator_topology` and `discover_network_topology` are both
thin, class-filtered wrappers around the general `discover_pci_topology`:

```python
from lab_11_worker_pool import discover_accelerator_topology, plan_accelerator_workers

topology = discover_accelerator_topology()
placements = plan_accelerator_workers(topology, workers=2)
for placement in placements:
    print(placement.device_address, placement.numa_node)
```

`plan_accelerator_workers` defaults to a `single-numa-node` policy, the same
name kubelet's Topology Manager uses: it rejects a request it cannot satisfy
from one NUMA node's accelerators rather than spreading it across nodes and
accepting the extra PCIe hop silently. Passing `single_numa_node=False` models
the more permissive `none` policy.

`discover_iommu_group` reads `/sys/kernel/iommu_groups/*/devices` and returns
every PCI address that shares one isolation group with the address given,
which is the set of functions that must be assigned to a virtual machine or
container together. Group membership is necessary information for planning a
passthrough, not a certificate that a single-device group is automatically
safe to hand to an unprivileged workload: that also depends on the guest or
container's own driver and kernel confinement, and on device firmware the
IOMMU never inspects.

`peer_to_peer_feasible` and `plan_transfer` estimate whether a GPU and a
network adapter share enough PCIe ancestry for a GPUDirect-style peer-to-peer
DMA path, or whether the safer choice is staging through a host-pinned
buffer on the network adapter's own NUMA node. Both accept the general
`PciFunction` type for either argument, since the adjacency estimate is the
same regardless of device class and a network adapter discovered through
`discover_network_topology` is never itself an `AcceleratorDevice`. Shared
ancestry is necessary but not sufficient: a downstream port with PCIe Access
Control Services (ACS) enabled can still redirect a peer-to-peer transaction
up to the root complex, which both functions accept as an
`acs_redirect_enabled` argument.

`diagnose_roce_readiness` checks an observed `RoceFabricConfig` against a
`RoceCongestionPolicy`, the congestion-control design one site has actually
chosen, rather than against a fixed protocol rule. RoCEv2 itself mandates
neither priority flow control, nor explicit congestion notification, nor a
minimum MTU. A site running a DCQCN-style design can require all three; a
site running a lossy, retransmission-based design such as IRN can supply a
policy that requires none of the flow-control settings, and the function will
not report a problem for their absence. `requires_ethernet_congestion_policy`
reports whether a fabric needs that kind of decision at all: RoCEv2 runs over
Ethernet, so choosing a congestion policy for it is specifically an Ethernet
decision, while native InfiniBand's link layer already carries credit-based
flow control and is not carried over Ethernet, so it needs no equivalent
Ethernet policy decision. That is narrower than saying InfiniBand needs no
congestion engineering at all; sizing buffer credits and switch bisection
bandwidth is still real work an InfiniBand operator has to do.

Every one of these functions reads a fixture directory tree or a plain
dataclass. The tests build sysfs-shaped fixtures the same way
`test_numa_topology_is_read_from_sysfs_contract` does, so the normal gate
proves the parsing and placement logic without a GPU, a PCIe switch, an
InfiniBand adapter or a RoCE fabric anywhere in the loop.

## Completion condition

The checkpoint is complete when thread concurrency remains bounded, visibility
timeouts redeliver abandoned work, shutdown drains accepted work, spawned
processes return the same values as serial execution, manager updates retain
every delivery, shared-memory slices cover the payload once, NUMA topology and
page-placement formats are parsed deterministically, worker placements remain
inside the allowed affinity set, accelerator topology and IOMMU group parsing
handle PCI sysfs formats deterministically, peer-to-peer feasibility and RoCE
readiness checks depend only on the typed inputs given to them, and
`pybootstrap check` exits zero.

The final `relay` service uses the thread path for blocking adapters and the
process path for CPU-heavy handlers. Release evidence records bounded queues,
spawn-safe messages, shared-memory ownership, and NUMA and accelerator-aware placement as
explicit runtime contracts.
## Python REPL debugging session

Inspect message contracts before starting child processes:

```pycon
>>> import inspect
>>> import lab_11_worker_pool as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Construct one picklable work item, inspect its type and representation, and
inspect the process-runner signature. Start processes only from a script guarded
by `if __name__ == "__main__":`.
