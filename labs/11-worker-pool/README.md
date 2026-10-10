# Lab 11 Thread and process workers with NUMA and accelerator-aware placement

## Goal and purpose

This lab builds the concurrent worker execution subsystem for SigRaft.
The package import name is `relay`. (This name is an internal project moniker;
the program does not relay network traffic.)

In distributed systems, executing background jobs requires matching each type
of workload to the right execution model:
1. **Threaded workers for I/O-bound jobs:** Threads pull jobs from a queue using
   time-limited visibility leases. When a worker leases a job, the queue hides
   it from other workers. If the worker finishes successfully, it acknowledges
   the job; if the worker crashes or times out, the lease expires and the job
   reappears on the queue for another worker to retry.
2. **Spawned processes for CPU-bound jobs:** To bypass Python's Global
   Interpreter Lock (GIL) and run computation across multiple CPU cores, child
   processes are spawned using an explicit `spawn` context. Processes exchange
   inputs and results over bounded queues, synchronize progress counters
   through manager proxies, and read large datasets from shared-memory blocks.
3. **NUMA discovery and memory locality:** On multi-socket servers, accessing
   memory attached to another CPU socket (remote memory) is significantly
   slower than accessing local memory. The lab reads Linux `sysfs` topology to
   balance workers across CPU nodes and bind processes to local memory.
4. **Accelerator and PCIe topology:** The lab inspects PCIe device trees to
   discover GPUs and RDMA network cards, identify IOMMU isolation groups, and
   plan direct peer-to-peer DMA transfers without unnecessary host RAM copies.

## Learning outcomes

By completing this lab, you will understand:
- How message visibility leases prevent duplicate execution while ensuring
  automatic recovery when workers crash.
- How to manage child process lifecycles, graceful shutdown, and shared memory
  cleanup without resource leaks.
- How Non-Uniform Memory Access (NUMA) affects memory latency and throughput.
- How Linux exposes CPU, memory, and PCIe accelerator topology through `sysfs`.

## Prerequisites and setup

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Threads, multiprocessing and shared memory are standard-library facilities.
NUMA bindings are an optional extra in `pyproject.toml`; no accelerator,
cluster or subscription is required for the gate.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

## Exercise 1 Preserve the visibility lease

Start with `worker_pool.py` and `queue.py`. Submit queued `RelayTask` objects,
cap the number of handlers in flight, and represent SIGTERM by calling
`request_shutdown`.

The pool must:

1. Stop leasing new work after shutdown is requested.
2. Let current handlers finish.
3. Acknowledge successful and failed work.
4. Leave unacknowledged work available after its visibility timeout.
5. Join every worker thread within the caller's deadline.

The in-memory queue lets you test leasing and redelivery without Azure.
It retains acknowledged entries and does not cap queued items, so bounded
handler concurrency does not mean bounded storage for a long-running service.

## Exercise 2 Send CPU work to spawned processes

Read `multicore.py`, then save this as `worker_example.py` in the lab and run
`python worker_example.py`. Do not paste process creation into the REPL:

```python
from lab_11_worker_pool import CpuWork, run_process_queue

if __name__ == "__main__":
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

## Exercise 3 Compare IPC primitives

Inter-process communication (IPC) carries work between the separate address
spaces used above. The queue example is one choice, not the only one.
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

## Exercise 4 Use a manager for coordination

Returning independent results needs no shared dictionary. Counting deliveries
from several workers does, so this exercise introduces a manager process that
owns that shared state.

Call `count_deliveries_with_manager` with repeated task identifiers. A manager
server owns the dictionary and lock; child processes hold proxies. The lock
covers the complete read-modify-write operation.

This is coordination, not a high-rate data path. Remove the lock and explain
why two proxy method calls are not one atomic increment. Compare manager
updates with returning per-worker dictionaries and reducing them in the
parent.

## Exercise 5 Separate payload from control

`sum_shared_bytes` creates one named shared-memory segment, divides it into
disjoint slices, and sends only the segment name and offsets to workers. Verify
that:

- every byte belongs to exactly one slice
- the parallel sum equals `sum(payload)`
- each mapping is closed
- the parent unlinks the shared-memory name once

Shared memory avoids sending the complete payload through every queue. It does
not supply locks, data validity, byte order, or recovery after a partial
write.

## Exercise 6 Inspect multicore capacity

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

## Exercise 7 Read NUMA topology

`discover_numa_topology` reads:

```text
/sys/devices/system/node/node*/cpulist
/sys/devices/system/node/node*/distance
```

`parse_numa_maps` summarizes `N0=pages`, `N1=pages`, and similar fields from
`/proc/PID/numa_maps`. The tests use fixtures so they pass on single-node
machines.

For an optional comparison on an approved multi-node host, use your own
benchmark. `YOUR_BENCHMARK` and `PID` below are placeholders for its importable
benchmark module and running process. They are not commands supplied by this
lab. Choose permitted NUMA nodes rather than assuming nodes 0 and 1 exist.

```bash
numactl --hardware
numactl --cpunodebind=0 --membind=0 python -m YOUR_BENCHMARK
numactl --cpunodebind=1 --membind=1 python -m YOUR_BENCHMARK
numastat -p PID
```

Allocate and initialize the payload after applying the placement policy.
Linux commonly places anonymous pages on the node of the CPU that first writes them.
CPU affinity alone does not select the memory node.

## Exercise 8 Balance and bind process workers

`plan_numa_workers` intersects discovered node CPUs with the process's allowed
affinity set. It rotates workers across nodes first, then across the CPUs within
each node. Apply a placement inside a spawned child before allocating its large
working set. The following is an illustrative fragment: choose `cpu_id`,
`node_id`, `payload_size` and `source` from the permitted host topology and
your benchmark before running it:

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
and random offsets. Write down elapsed time, throughput, affinity,
`/proc/PID/numa_maps`, `numastat -p PID`, and permitted `perf stat` cache
counters. Recreate and initialize the buffer after every policy change.

## Exercise 9 Plan accelerator topology without a GPU

`accelerators.py` extends the same sysfs discipline from Exercise 7 to PCI
devices generally. sysfs exposes hardware information as files.
PCIe connects devices such as graphics processing units (GPUs) and network
adapters to the host. `PciFunction` is the general object produced by walking
`/sys/devices`: a bus address, a NUMA affinity hint, a class code and the
chain of PCIe bridge ancestors above it. `AcceleratorDevice` names that same
object when returned by accelerator-filtered discovery. It is a naming alias,
not a separate Python type or a device-class validator. Network discovery
returns `PciFunction` objects with network-controller class codes.
`discover_accelerator_topology` and `discover_network_topology` are both
thin, class-filtered wrappers around the general `discover_pci_topology`:

```python
from lab_11_worker_pool import discover_accelerator_topology, plan_accelerator_workers

topology = discover_accelerator_topology()
placements = plan_accelerator_workers(topology, workers=2)
for placement in placements:
    print(placement.device_address, placement.numa_node)
```

`plan_accelerator_workers` defaults to selecting all requested accelerators
from one reported NUMA node. If no node has enough devices it raises an error;
passing `single_numa_node=False` allows selection across nodes. This resembles
one locality decision in kubelet's Topology Manager, not its complete admission
algorithm. It neither aligns CPUs and memory nor reserves devices, and it
groups unknown node values (`-1`) together. Reject unknown locality before
using a plan that requires a known NUMA node.

The input-output memory management unit (IOMMU) restricts device memory access.
`discover_iommu_group` reads `/sys/kernel/iommu_groups/*/devices` and returns
every PCI address that shares one isolation group with the address given.
Treat the group as one isolation unit rather than assigning its functions to
mutually untrusted owners. Group membership is necessary information for planning a
passthrough, not a certificate that a single-device group is automatically
safe to hand to an unprivileged workload: that also depends on the guest or
container's own driver and kernel confinement, and on device firmware the
IOMMU never inspects.

`peer_to_peer_feasible` and `plan_transfer` estimate whether a GPU and a
network adapter share enough PCIe ancestry for a GPUDirect-style peer-to-peer
DMA path, or whether the safer choice is staging through a host-pinned
buffer on the network adapter's reported NUMA node. Both accept `PciFunction`
objects and compare their ancestor paths; neither initiates DMA or allocates a
buffer. A shared path does not establish hardware support: a port with PCIe Access
Control Services (ACS) enabled can still redirect a peer-to-peer transaction
up to the root complex, which both functions accept as an
`acs_redirect_enabled` argument.

Remote direct memory access (RDMA) lets a network adapter transfer data without
the usual application-copy path. RoCE carries RDMA over Ethernet.
`diagnose_roce_readiness` checks an observed `RoceFabricConfig` against a
`RoceCongestionPolicy`, the congestion-control design one site has actually
chosen, rather than against a fixed protocol rule. Priority flow control (PFC)
pauses selected Ethernet traffic classes; explicit congestion notification
(ECN) marks congestion for endpoints. The maximum transmission unit (MTU)
limits packet size. This model's PFC, ECN and minimum-MTU requirements come
from site policy. A Data Center Quantized Congestion Notification (DCQCN)
deployment can require all three; a loss-tolerant design such as Improved
RoCE NIC (IRN) can supply a policy that requires none of the flow-control
settings, and the function will not report a problem for their absence.
`requires_ethernet_congestion_policy` returns true for RoCEv2 because it uses
Ethernet, and false for native InfiniBand because it does not. InfiniBand still
needs congestion engineering, including buffer-credit and bandwidth planning.

Discovery reads sysfs by default; pass a fixture root to inspect test data
instead of host hardware. The tests build sysfs-shaped fixtures the same way
`test_numa_topology_is_read_from_sysfs_contract` does, so the normal gate
checks parsing and placement decisions without a GPU, a PCIe switch, an
InfiniBand adapter or a RoCE fabric.

## Tests

The lab has two kinds of test. `tests/test_lab_11_worker_pool.py` holds the
unit tests. They check the queue, the pool, the process helpers and the
topology parsers one at a time against fixtures and fakes.

`tests/test_functional.py` holds the functional tests. They drive
`RelayWorkerPool` through `submit`, `start`, `snapshot`, `request_shutdown`
and `join` over the in-memory queue with a manual clock. They check that
jobs move from `queued` through `running` to `succeeded` or `failed`, that a
job abandoned by a lost worker is delivered again after its lease expires,
and that a job submitted in another state is rejected.

Run each kind alone with `pytest tests/test_lab_11_worker_pool.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Completion condition

The lab is complete when thread concurrency remains bounded, visibility
timeouts redeliver abandoned work, shutdown drains accepted work, spawned
processes return the same values as serial execution, manager updates retain
every delivery, shared-memory slices cover the payload once, NUMA topology and
page-placement formats are parsed deterministically, worker placements remain
inside the allowed affinity set, accelerator topology and IOMMU group parsing
handle PCI sysfs formats deterministically, peer-to-peer feasibility and RoCE
readiness checks depend only on the typed inputs given to them, and
`pybootstrap check` exits zero.

These independently runnable paths teach execution choices for the SigRaft
job-management web service; Lab 39 does not start this worker pool. Keep the
test results that show process-queue limits, picklable messages, shared-memory
cleanup and placement decisions separate from measurements on real hardware.

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
>>> work = lab.CpuWork("task-17", 17)
>>> type(work), repr(work)
>>> inspect.signature(lab.run_process_queue)
```

Construct one picklable work item, inspect its type and representation, and
inspect the process-runner signature. Start processes only from a script guarded
by `if __name__ == "__main__":`.

Run `pytest -q tests/test_lab_11_worker_pool.py` for the lab behaviors.
`pybootstrap check` must exit 0; exit 1 means findings and exit 2 means a gate
could not run. Confirm children are joined and shared-memory names unlinked,
then remove `worker_example.py` if no longer needed. Do not leave probes or
placement changes running on a shared host. Fixture topology checks model decisions,
not accelerator DMA, throughput or device isolation.
